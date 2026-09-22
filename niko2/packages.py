"""Niko 2 package manager core (Alpha 16).

This module owns everything about Niko *packages* (reusable libraries),
while ``modules.py`` owns how ``import`` statements resolve to files.

What lives here
---------------
* ``niko.toml`` manifest parsing and validation (``parse_manifest``).
* ``niko2 get <source>`` installation (``install_package``) -- the ONLY
  place in the whole toolchain that touches the network. Compiling,
  running, checking, and the LSP/debugger resolve packages from the
  local cache and never go near the network.
* The ``pkg:`` import form used by ``modules.resolve_import``:
  ``import "pkg:<name>/path/to/file.niko" as alias`` resolves to
  ``<cache>/<name>-<version>/path/to/file.niko``. The version comes from
  the project's ``niko.lock`` when present, otherwise the newest cached
  version.
* Lockfile helpers: ``lock_packages_for_project`` produces the
  ``"packages"`` table that ``niko2 lock`` writes.

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

This module imports only the standard library: it must stay importable
from ``modules.py`` and ``project.py`` without creating import cycles.
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
from dataclasses import dataclass
from pathlib import Path

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
    Every failure raises ``PackageError`` naming the file and the problem.
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

    return PackageManifest(name=name, version=version, entry=entry,
                           description=description, manifest_path=path)


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


def _write_source_record(pkg_dir, source, kind):
    (Path(pkg_dir) / SOURCE_RECORD).write_text(
        json.dumps({'source': source, 'kind': kind}, indent=2) + '\n',
        encoding='utf8')


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


def lock_packages_for_project(root, cache_root=None):
    """Build the ``"packages"`` lockfile table for a project: every
    package imported via ``pkg:`` anywhere under *root*, pinned to a
    version with its install source.

    Rules: a previously locked version that is still installed is kept
    (re-locking never silently upgrades); otherwise the newest cached
    version is pinned. ``source`` comes from the cache's
    ``.niko-source.json`` provenance record (falling back to the old lock
    entry, then ``"unknown"``). An imported package that isn't installed
    at all is a hard error naming ``niko2 get``.
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
        source = (read_source_record(cache_dir_for(name, version, cache_root)).get('source')
                  or (prev.get('source') if isinstance(prev, dict) else None)
                  or 'unknown')
        locked[name] = {'version': version, 'source': source}
    return locked
