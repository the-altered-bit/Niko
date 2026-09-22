"""Niko 2 package manager core (Alpha 16).

This module owns everything about Niko *packages* (reusable libraries),
while ``modules.py`` owns how ``import`` statements resolve to files.

What lives here
---------------
* ``niko.toml`` manifest parsing and validation (``parse_manifest``).
* ``niko2 get <source>`` installation (``install_package``) -- installs
  from a local directory or a git URL. Registry installs
  (``niko2 get <name>[@<range>]``) live in ``registry.py`` and share the
  cache layout and lockfile helpers here.
* The ``pkg:`` import form used by ``modules.resolve_import``:
  ``import "pkg:<name>/path/to/file.niko" as alias`` resolves to
  ``<cache>/<name>-<version>/path/to/file.niko``. The version comes from
  the project's ``niko.lock`` when present, otherwise the newest cached
  version.
* Lockfile helpers: ``lock_packages_for_project`` produces the
  ``"packages"`` table that ``niko2 lock`` writes; ``write_package_pin``
  creates/updates a single pin (used by registry installs).

What lives in ``registry.py`` (Alpha 19) instead
------------------------------------------------
The package *registry*: version-range solving (``semver.py``), the
registry index protocol, ``niko2 get <name>[@<range>]``,
``niko2 publish``, and ``niko2 get --update``. ``get`` and ``publish``
are the only commands that touch the network -- compiling, running,
checking, and the LSP/debugger resolve packages from the local cache
and never go near the network.

Deliberate limits (Alpha 16 -- no registry, no solver)
------------------------------------------------------
* There is no package registry server and no ``publish`` command.
  ``niko2 get`` installs from a local directory or a git URL only.
* There is no version-range solving. ``niko2 get`` installs exactly the
  version described by the source's own ``niko.toml``; ``import`` takes no
  version -- the lockfile pins the version (``niko2 lock``), and the
  resolver prefers the locked version, falling back to the newest cached
  one when there is no lockfile.
* The cache holds a plain directory copy of the package (for git
  sources the ``.git`` metadata is stripped). To pick up upstream
  changes, run ``niko2 get <url> --force`` again.

Cache layout
------------
``~/.niko/packages/<name>-<version>/``. The directory is overridable
with the ``NIKO_PKG_CACHE`` environment variable (one name, documented
here). Each cached package also carries a ``.niko-source.json`` file
recording where it came from (``{"source": ..., "kind": "git"|"local"}``),
so ``niko2 lock`` can record provenance without re-asking the user.

This module imports only the standard library and ``semver`` (which is
itself stdlib-only): it must stay importable from ``modules.py`` and
``project.py`` without creating import cycles. ``registry.py`` imports
*this* module, never the other way round.
Errors are raised as ``PackageError`` (plain English); callers that have
source positions (e.g. ``modules.resolve_import``) translate them into
``ImportErrorNiko`` with line/col.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import semver

try:
    import tomllib
except ImportError:  # Python < 3.11 -- tomllib is stdlib from 3.11 on
    tomllib = None

#: Prefix that marks a package import: import "pkg:<name>/file.niko" as x
PKG_IMPORT_PREFIX = 'pkg:'

#: Environment variable overriding the package cache directory.
CACHE_ENV_VAR = 'NIKO_PKG_CACHE'

#: File inside each cached package recording where it was installed from.
SOURCE_RECORD = '.niko-source.json'

_NAME_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_-]*$')
_VERSION_RE = re.compile(r'^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$')


class PackageError(ValueError):
    """A package-manager error, already worded for humans."""


@dataclass
class PackageManifest:
    """A validated ``niko.toml`` manifest."""
    name: str
    version: str
    entry: str = 'main.niko'
    description: str = ''
    manifest_path: Path | None = None
    #: Package dependencies: name -> version-range string, from the
    #: manifest's top-level ``[dependencies]`` table (Alpha 19). Empty
    #: when the package declares none.
    dependencies: dict = field(default_factory=dict)


@dataclass
class InstallResult:
    """Outcome of ``install_package``."""
    manifest: PackageManifest
    path: Path          # cache directory the package now lives in
    fresh: bool         # False when it was already cached (no work done)


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def parse_manifest(path):
    """Parse and validate the ``niko.toml`` at *path*.

    Canonical form is a ``[package]`` table::

        [package]
        name = "acme-utils"
        version = "1.2.0"
        entry = "main.niko"        # optional, defaults to main.niko
        description = "..."        # optional free text

    For backwards compatibility with Alpha 4-era manifests, flat
    top-level keys (``name = ...`` with no ``[package]`` table) are
    accepted too; when both are present the ``[package]`` table wins.
    Unknown fields are ignored (forward compatibility -- old toolchains
    keep working when new optional fields appear).

    Rules enforced: ``name`` is letters/digits/``-``/``_`` starting with
    a letter; ``version`` is strict ``X.Y.Z`` (no leading zeros);
    ``entry`` defaults to ``main.niko`` and must name a ``.niko`` file.
    An optional top-level ``[dependencies]`` table maps package names to
    version-range strings (``greet = "^1.0"``); names follow the package
    name rule and ranges must parse (see ``semver``). Every failure
    raises ``PackageError`` naming the file and the problem.
    """
    path = Path(path)
    if tomllib is None:
        raise PackageError(
            f'cannot read {path}: reading niko.toml needs Python 3.11+ (tomllib)')
    try:
        with path.open('rb') as f:
            data = tomllib.load(f)
    except OSError as e:
        raise PackageError(f'cannot read {path}: {e}')
    except tomllib.TOMLDecodeError as e:
        raise PackageError(f'{path}: bad TOML: {e}')
    if not isinstance(data, dict):
        raise PackageError(f'{path}: manifest must be a TOML table')

    pkg = data.get('package')
    if isinstance(pkg, dict):
        fields = pkg
    else:
        fields = data  # Alpha 4-era flat manifest

    def need(key, what):
        value = fields.get(key)
        if value is None:
            raise PackageError(f'{path}: missing required field "{key}" ({what})')
        if not isinstance(value, str):
            raise PackageError(f'{path}: "{key}" must be text, got {type(value).__name__}')
        return value

    name = need('name', 'package name').strip()
    if not _NAME_RE.match(name):
        raise PackageError(
            f'{path}: bad package name "{name}" -- use letters, numbers, '
            '"-" and "_", starting with a letter')
    version = need('version', 'package version like "1.2.0"').strip()
    if not _VERSION_RE.match(version):
        raise PackageError(
            f'{path}: bad version "{version}" -- use strict X.Y.Z, e.g. "1.2.0"')
    entry = fields.get('entry', 'main.niko')
    if not isinstance(entry, str) or not entry.strip():
        raise PackageError(f'{path}: "entry" must be a .niko file path')
    entry = entry.strip()
    if Path(entry).suffix != '.niko':
        raise PackageError(f'{path}: "entry" must name a .niko file, got "{entry}"')
    description = fields.get('description', '')
    if not isinstance(description, str):
        raise PackageError(f'{path}: "description" must be text')

    dependencies = _parse_dependencies(path, data)

    return PackageManifest(name=name, version=version, entry=entry,
                           description=description, manifest_path=path,
                           dependencies=dependencies)


def _parse_dependencies(path, data):
    """Validate the manifest's top-level ``[dependencies]`` table.

    Returns ``{name: range_string}`` (``{}`` when absent). Names follow
    the package name rule; every range must parse via ``semver``.
    """
    deps = data.get('dependencies', {})
    if deps is None:
        return {}
    if not isinstance(deps, dict):
        raise PackageError(
            f'{path}: "[dependencies]" must be a table of '
            'name = "range" pairs, e.g.\n[dependencies]\ngreet = "^1.0"')
    out = {}
    for dep_name, dep_range in deps.items():
        if not _NAME_RE.match(dep_name):
            raise PackageError(
                f'{path}: bad dependency name "{dep_name}" -- use letters, '
                'numbers, "-" and "_", starting with a letter')
        if not isinstance(dep_range, str) or not dep_range.strip():
            raise PackageError(
                f'{path}: dependency "{dep_name}" needs a version range '
                'string, e.g. "^1.0"')
        try:
            semver.parse_range(dep_range)
        except semver.SemverError as e:
            raise PackageError(
                f'{path}: dependency "{dep_name}": {e}')
        out[dep_name] = dep_range.strip()
    return out


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def default_cache_root():
    """Cache directory: ``$NIKO_PKG_CACHE`` or ``~/.niko/packages``."""
    override = os.environ.get(CACHE_ENV_VAR, '').strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / '.niko' / 'packages'


def cache_dir_for(name, version, cache_root=None):
    """``<cache>/<name>-<version>`` for a package."""
    return Path(cache_root or default_cache_root()) / f'{name}-{version}'


def _version_key(version):
    m = _VERSION_RE.match(version or '')
    if not m:
        return None
    return tuple(int(g) for g in m.groups())


def installed_versions(name, cache_root=None):
    """``[(version_tuple, version_str, path)]`` installed for *name*,
    oldest first. Directories that don't parse as ``<name>-X.Y.Z`` are
    skipped."""
    root = Path(cache_root or default_cache_root())
    found = []
    if root.is_dir():
        prefix = f'{name}-'
        for child in root.iterdir():
            if not child.is_dir() or not child.name.startswith(prefix):
                continue
            key = _version_key(child.name[len(prefix):])
            if key is not None:
                found.append((key, child.name[len(prefix):], child))
    found.sort(key=lambda t: t[0])
    return found


def read_source_record(pkg_dir):
    """The ``.niko-source.json`` provenance record, or ``{}``."""
    try:
        return json.loads((Path(pkg_dir) / SOURCE_RECORD).read_text(encoding='utf8'))
    except (OSError, ValueError):
        return {}


def _write_source_record(pkg_dir, source, kind, extra=None):
    record = {'source': source, 'kind': kind}
    if extra:
        record.update(extra)
    (Path(pkg_dir) / SOURCE_RECORD).write_text(
        json.dumps(record, indent=2) + '\n', encoding='utf8')


# ---------------------------------------------------------------------------
# Install: `niko2 get` -- the ONLY network touchpoint in the toolchain
# ---------------------------------------------------------------------------

def _is_git_source(source):
    s = source.strip()
    return (s.startswith(('https://', 'http://', 'git@', 'ssh://', 'file://'))
            or s.endswith('.git'))


def _git_clone(url, dest):
    git = shutil.which('git')
    if git is None:
        raise PackageError(
            'git is not installed (or not on PATH) -- install git to '
            f'fetch "{url}", or point niko2 get at a local directory instead')
    # Shallow: we only ever need the default branch's working tree.
    proc = subprocess.run([git, 'clone', '--depth', '1', url, str(dest)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or '').strip().splitlines()
        tail = detail[-1] if detail else 'unknown error'
        raise PackageError(f'could not clone "{url}": {tail}')


def install_package(source, *, cache_root=None, force=False):
    """Install a package into the cache (``niko2 get``).

    *source* is a local directory (must contain ``niko.toml``) or a git
    URL (``https://...``, ``git@...``, ``ssh://...``, ``file://...`` --
    ``file://`` URLs are handy for tests). Git sources are shallow-cloned
    (``--depth 1``); the cache keeps a plain directory copy with the
    ``.git`` metadata stripped.

    Validation: the manifest must exist and be valid, and the ``entry``
    file must exist in the package directory. The manifest's name/version
    are authoritative -- the source directory's own name is ignored.

    Idempotent: when ``<name>-<version>`` is already cached, nothing is
    copied and ``InstallResult.fresh`` is False (``force=True`` reinstalls).

    Network access happens only here -- never during compile/run/check.
    """
    src = (source or '').strip()
    if not src:
        raise PackageError('niko2 get needs a source: a package directory or a git URL')
    root = Path(cache_root or default_cache_root())

    if _is_git_source(src):
        # NETWORK: the one place the toolchain is allowed to use it.
        tmp = Path(tempfile.mkdtemp(prefix='niko-get-'))
        try:
            _git_clone(src, tmp / 'pkg')
            manifest = parse_manifest(tmp / 'pkg' / 'niko.toml')
            _check_entry(tmp / 'pkg', manifest)
            dest = cache_dir_for(manifest.name, manifest.version, root)
            if dest.is_dir() and not force:
                return InstallResult(manifest, dest, fresh=False)
            if dest.is_dir():
                shutil.rmtree(dest)
            shutil.copytree(tmp / 'pkg', dest,
                            ignore=shutil.ignore_patterns('.git'))
            _write_source_record(dest, src, 'git')
            return InstallResult(manifest, dest, fresh=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    src_dir = Path(src).expanduser()
    if not src_dir.exists():
        raise PackageError(
            f'"{src}" is not a directory and not a git URL -- niko2 get takes '
            'a package directory or a git URL (https://..., git@..., file://...)')
    if not src_dir.is_dir():
        raise PackageError(f'"{src}" is not a directory')
    manifest_path = src_dir / 'niko.toml'
    if not manifest_path.is_file():
        raise PackageError(f'"{src}" has no niko.toml -- not a Niko package')
    manifest = parse_manifest(manifest_path)
    _check_entry(src_dir, manifest)
    dest = cache_dir_for(manifest.name, manifest.version, root)
    if dest.is_dir() and not force:
        return InstallResult(manifest, dest, fresh=False)
    if dest.is_dir():
        shutil.rmtree(dest)
    shutil.copytree(src_dir, dest)
    _write_source_record(dest, str(src_dir.resolve()), 'local')
    return InstallResult(manifest, dest, fresh=True)


def _check_entry(src_dir, manifest):
    entry = src_dir / manifest.entry
    if not entry.is_file():
        raise PackageError(
            f'{manifest.manifest_path}: entry "{manifest.entry}" not found '
            f'in the package directory {src_dir}')


# ---------------------------------------------------------------------------
# Resolve: `import "pkg:<name>/path.niko"` -- offline, cache only
# ---------------------------------------------------------------------------

def resolve_package(name, locked_version=None, cache_root=None):
    """Directory of an installed package. Prefers *locked_version* (from
    ``niko.lock``) when given, else the newest cached version.

    NOTE: no network here -- callers get "not installed" errors telling
    the user to run ``niko2 get``.
    """
    root = cache_root or default_cache_root()
    if locked_version:
        dest = cache_dir_for(name, locked_version, root)
        if dest.is_dir():
            return dest
        raise PackageError(
            f'package "{name}" is locked to version {locked_version} but it '
            f'is not installed -- run \'niko2 get <source>\' to install it')
    versions = installed_versions(name, root)
    if not versions:
        raise PackageError(
            f'unknown package "{name}" -- run \'niko2 get <source>\' to install it first')
    return versions[-1][2]


def resolve_package_file(name, rel, locked_version=None, cache_root=None):
    """Resolve ``pkg:<name>/<rel>`` to an absolute ``.niko`` file path.

    Raises ``PackageError`` for unknown packages, bad paths, ``..``
    escapes, and files missing inside the cached package.
    """
    if Path(rel).suffix != '.niko':
        raise PackageError(
            f'import "pkg:{name}/{rel}" must name a .niko file, '
            f'e.g. import "pkg:{name}/main.niko" as x')
    pkg_dir = resolve_package(name, locked_version, cache_root)
    target = (pkg_dir / rel).resolve()
    if target != pkg_dir and pkg_dir not in target.parents:
        raise PackageError(
            f'import "pkg:{name}/{rel}" escapes the package directory')
    if not target.is_file():
        raise PackageError(
            f'cannot find module "pkg:{name}/{rel}" '
            f'(no such file in {pkg_dir.name})')
    return target


def resolve_pkg_spec(spec, locked=None, cache_root=None):
    """Resolve a full ``pkg:<name>/path.niko`` spec string to a file path.
    *locked* maps package name -> locked version (from ``niko.lock``)."""
    body = spec[len(PKG_IMPORT_PREFIX):]
    name, sep, rel = body.partition('/')
    if not sep or not name.strip() or not rel.strip():
        raise PackageError(
            f'import "{spec}" needs a package name and a file path, '
            'e.g. import "pkg:my-lib/text.niko" as text')
    name = name.strip()
    if not _NAME_RE.match(name):
        raise PackageError(f'import "{spec}": bad package name "{name}"')
    locked = locked or {}
    return resolve_package_file(name, rel.strip(), locked.get(name),
                                cache_root)


def locked_package_versions(start_dir):
    """``{name: version}`` pinned by the nearest enclosing ``niko.lock``.

    Walks up from *start_dir*; no lockfile means no pins (``{}``). A
    corrupt lockfile is a hard error -- silently ignoring pins would
    resolve the wrong code.
    """
    d = Path(start_dir).resolve()
    for cur in [d, *d.parents]:
        lock = cur / 'niko.lock'
        if lock.is_file():
            try:
                data = json.loads(lock.read_text(encoding='utf8'))
            except (OSError, ValueError) as e:
                raise PackageError(f'cannot read {lock}: {e}')
            pkgs = data.get('packages', {})
            if not isinstance(pkgs, dict):
                return {}
            return {k: v.get('version') for k, v in pkgs.items()
                    if isinstance(v, dict) and v.get('version')}
    return {}


# ---------------------------------------------------------------------------
# Project scanning: `niko2 deps` / `niko2 lock`
# ---------------------------------------------------------------------------

def collect_package_imports(root):
    """``{relative_path: [pkg: specs]}`` for every ``.niko`` file under
    *root* that imports via the ``pkg:`` form. Files that fail to parse
    are skipped (same leniency as the ``use``-based scan in project.py)."""
    from .parser import parse
    from .ast import ImportStmt
    root = Path(root)
    result = {}
    for path in sorted(root.rglob('*.niko')):
        if path.name.startswith('.'):
            continue
        try:
            tree = parse(path.read_text(encoding='utf8'))
        except Exception:
            continue
        specs = sorted({n.path.strip() for n in tree.body
                        if isinstance(n, ImportStmt)
                        and n.path.strip().startswith(PKG_IMPORT_PREFIX)})
        if specs:
            result[path.relative_to(root).as_posix()] = specs
    return result


def _read_lock_packages(root):
    """The existing ``"packages"`` table of ``<root>/niko.lock`` (``{}``)."""
    lock = Path(root) / 'niko.lock'
    if not lock.is_file():
        return {}
    try:
        data = json.loads(lock.read_text(encoding='utf8'))
    except (OSError, ValueError):
        return {}
    pkgs = data.get('packages')
    return pkgs if isinstance(pkgs, dict) else {}


def _closure_conflict(dep, parent, dep_range, pinned_version, origin_desc):
    """Plain-English error for two live requirements on one package."""
    return (
        f'conflicting requirements for package "{dep}": "{parent}" needs '
        f'"{dep_range}", but it is already pinned to {pinned_version} '
        f'({origin_desc}) -- the lockfile holds one version per package. '
        f'To fix: run \'niko2 get {dep}@<range>\' with a range both '
        f'requirements accept, then \'niko2 lock\' again.')


def _transitive_closure_pins(seed_pins, prior_pins, cache_root=None):
    """Resolve the transitive dependency closure for *seed_pins*.

    *seed_pins* maps package name -> lockfile entry for the packages
    already pinned (the project's top-level imports); *prior_pins* is
    the previous lockfile's ``"packages"`` table (it may pin names that
    are no longer top-level imports). Returns ``{name: entry}`` for
    every TRANSITIVE dependency (seed names never appear), each entry
    shaped ``{'version', 'source', 'range'}``.

    Resolution rule -- mirrors the installer's preference for cached
    versions (``registry._ensure_dependencies`` leaves a satisfied dep
    alone) and the top-level "re-locking never silently upgrades" rule:
    a dependency that is already pinned -- in *seed_pins*, earlier in
    this walk, or in *prior_pins* -- keeps its pin when the pinned
    version is installed and satisfies the requesting parent's range;
    otherwise the newest installed version satisfying the range is
    pinned. A dependency with no installed version satisfying the range
    is a hard error naming ``niko2 get``. Two *live* requirements (both
    in the current graph) that admit no common pinned version are a
    hard conflict error naming both. A stale *prior_pins* entry that no
    longer satisfies is simply re-resolved, never an error.

    The recorded ``range`` is the range requested by the parent whose
    requirement caused the pin (first parent wins when several parents
    agree on the pinned version). Cycle-safe: each package's own
    dependencies are expanded at most once, so a<->b terminates.
    """
    table = {n: dict(e) for n, e in seed_pins.items()}
    origins = {n: 'pinned by the project' for n in seed_pins}
    pins = {}
    expanded = set()
    queue = sorted(table)
    while queue:
        name = queue.pop(0)
        if name in expanded:
            continue
        expanded.add(name)
        version = table[name]['version']
        manifest = parse_manifest(
            cache_dir_for(name, version, cache_root) / 'niko.toml')
        for dep, dep_range in sorted(manifest.dependencies.items()):
            if dep in table:
                # Pinned by the project, by this walk, or adopted from
                # the previous lockfile below: keep it when it
                # satisfies this parent too; otherwise the two live
                # requirements genuinely conflict.
                if semver.satisfies(table[dep]['version'], dep_range):
                    continue
                raise PackageError(_closure_conflict(
                    dep, name, dep_range, table[dep]['version'],
                    origins.get(dep, 'pinned by the project')))
            prior = prior_pins.get(dep)
            if (isinstance(prior, dict) and prior.get('version')
                    and cache_dir_for(dep, prior['version'],
                                      cache_root).is_dir()
                    and semver.satisfies(prior['version'], dep_range)):
                # A previous lockfile's pin that still fits: adopt it
                # (re-locking never silently upgrades).
                table[dep] = dict(prior)
                pins[dep] = dict(prior)
                origins[dep] = (
                    'pinned by the previous lockfile'
                    + (f' (as "{prior.get("range")}")'
                       if prior.get('range') else ''))
                queue.append(dep)
                continue
            cands = [v for _, v, _ in installed_versions(dep, cache_root)
                     if semver.satisfies(v, dep_range)]
            if not cands:
                raise PackageError(
                    f'package "{dep}" is required by "{name}" '
                    f'("{dep_range}") but no installed version satisfies '
                    f'that range -- run \'niko2 get {dep}@{dep_range}\' '
                    'to install it first')
            best = cands[-1]  # installed_versions returns oldest-first
            record = read_source_record(
                cache_dir_for(dep, best, cache_root))
            entry = {'version': best,
                     'source': record.get('source') or 'unknown',
                     'range': dep_range}
            table[dep] = entry
            pins[dep] = entry
            origins[dep] = f'required as "{dep_range}" by "{name}"'
            queue.append(dep)
    return pins


def pin_closure_under(lock_dir, top_name, cache_root=None):
    """Recompute lockfile pins for the dependency closure under *top_name*.

    Used by ``niko2 get --update <name>`` after the top-level pin is
    rewritten: the updated package's ``[dependencies]`` (name -> range,
    from its newly installed manifest) are resolved with the same rule
    as ``niko2 lock`` (see ``_transitive_closure_pins``) and written
    back with ``write_package_pin``. Returns the ``{name: entry}`` pins
    written.

    Only the closure beneath *top_name* is touched: pins belonging to
    other top-level packages are left alone, and stale entries (a dep
    the new version no longer needs) are left in place -- they are
    harmless, and the next ``niko2 lock`` re-derives the whole table
    from scratch and drops them.
    """
    prior = _read_lock_packages(lock_dir)
    top = prior.get(top_name)
    if not isinstance(top, dict) or not top.get('version'):
        raise PackageError(
            f'package "{top_name}" is not pinned in '
            f'{Path(lock_dir) / "niko.lock"}')
    new_pins = _transitive_closure_pins({top_name: dict(top)}, prior,
                                        cache_root)
    for dep, entry in sorted(new_pins.items()):
        write_package_pin(lock_dir, dep, entry['version'],
                          entry['source'], entry.get('range'))
    return new_pins


def lock_packages_for_project(root, cache_root=None):
    """Build the ``"packages"`` lockfile table for a project: every
    package imported via ``pkg:`` anywhere under *root*, pinned to a
    version with its install source -- plus the full transitive
    dependency closure beneath them (Alpha 24).

    Rules: a previously locked version that is still installed is kept
    (re-locking never silently upgrades); otherwise the newest cached
    version is pinned. ``source`` comes from the cache's
    ``.niko-source.json`` provenance record (falling back to the old lock
    entry, then ``"unknown"``). For registry installs the requested
    ``range`` is recorded too (previous lock entry first, then the
    install's recorded range); entries without a range simply omit the
    field, keeping the Alpha 16 ``{version, source}`` shape
    backwards-compatible. An imported package that isn't installed
    at all is a hard error naming ``niko2 get``.

    Transitive entries have the same ``{version, source, range?}``
    shape; their ``range`` is the range requested by the parent package
    whose requirement caused the pin (see ``_transitive_closure_pins``
    for the exact resolution rule, cycle handling, and conflict
    errors). Extra entries are harmless to ``locked_package_versions``,
    which reads only ``{name: version}`` pairs.
    """
    root = Path(root)
    names = sorted({spec[len(PKG_IMPORT_PREFIX):].partition('/')[0]
                    for specs in collect_package_imports(root).values()
                    for spec in specs})
    if not names:
        return {}
    existing = _read_lock_packages(root)
    locked = {}
    for name in names:
        prev = existing.get(name)
        prev_version = prev.get('version') if isinstance(prev, dict) else None
        if prev_version and cache_dir_for(name, prev_version, cache_root).is_dir():
            version = prev_version
        else:
            versions = installed_versions(name, cache_root)
            if not versions:
                raise PackageError(
                    f'package "{name}" is imported but not installed -- '
                    "run 'niko2 get <source>' to install it first")
            version = versions[-1][1]
        record = read_source_record(cache_dir_for(name, version, cache_root))
        source = (record.get('source')
                  or (prev.get('source') if isinstance(prev, dict) else None)
                  or 'unknown')
        entry = {'version': version, 'source': source}
        prev_range = prev.get('range') if isinstance(prev, dict) else None
        rec_range = record.get('range') if record.get('kind') == 'registry' else None
        if prev_range or rec_range:
            entry['range'] = prev_range or rec_range
        locked[name] = entry
    # Alpha 24: pin the full transitive closure, not just the top-level
    # imports -- this is what makes a committed lockfile reproducible.
    locked.update(_transitive_closure_pins(locked, existing, cache_root))
    return locked


def find_lock_dir(start):
    """Nearest enclosing directory of *start* containing ``niko.lock``.

    Returns None when there is no lockfile on the way up.
    """
    d = Path(start).resolve()
    for cur in [d, *d.parents]:
        if (cur / 'niko.lock').is_file():
            return cur
    return None


def write_package_pin(lock_dir, name, version, source, range=None):
    """Create or update one entry of ``<lock_dir>/niko.lock``.

    Only the ``"packages"`` table entry for *name* is touched; the rest
    of the lockfile (including ``"dependencies"``) is preserved, and a
    missing lockfile is created with the standard shape. The ``range``
    field is written only when given, keeping old ``{version, source}``
    entries valid.
    """
    lock = Path(lock_dir) / 'niko.lock'
    try:
        data = json.loads(lock.read_text(encoding='utf8')) if lock.is_file() else {}
    except ValueError as e:
        raise PackageError(f'cannot read {lock}: {e}')
    if not isinstance(data, dict):
        data = {}
    pkgs = data.get('packages')
    if not isinstance(pkgs, dict):
        pkgs = {}
        data['packages'] = pkgs
    data.setdefault('version', 1)
    data.setdefault('dependencies', {})
    entry = {'version': version, 'source': source}
    if range:
        entry['range'] = range
    pkgs[name] = entry
    lock.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n',
                    encoding='utf8')
    return lock
