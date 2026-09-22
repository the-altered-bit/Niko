"""Package registry support: index format, config, install/publish (Alpha 19).

A *registry* is an index document plus the tarballs it points at. Two
flavours are supported:

* **Local directory registry** -- a directory with this layout::

      <registry>/
          index.json
          tarballs/<name>-<version>.tar.gz

  Everything works hermetically (no network), which is also how the
  test-suite exercises this module.

* **Remote registry** -- ``NIKO_REGISTRY`` (or the config file) points at
  the index document's ``http(s)://`` URL. ``niko2 get`` can install from
  it; ``niko2 publish`` refuses (no auth story yet -- plain-English
  error telling the user to use a local directory registry). Fetches
  use a short timeout (10s) and report plain-English errors for the
  common failure modes (DNS failure, connection refused, timeout, HTTP
  status).

Index document format (JSON)::

    {
      "packages": {
        "<name>": {
          "<version>": {
            "url": "tarballs/<name>-<version>.tar.gz",
            "sha256": "<hex sha256 of the tarball>",
            "description": "...",                       // optional
            "dependencies": {"<name>": "<range>", ...}  // optional
          }
        }
      }
    }

``url`` may be relative (resolved against the registry: the directory
for a local registry, the index URL's directory for a remote one) or an
absolute ``http(s)://`` / ``file://`` URL. ``sha256`` is mandatory --
``get`` refuses to install an entry without one, and verifies the
downloaded bytes against it (mismatch = hard error, bad file deleted).

Registry selection: the ``NIKO_REGISTRY`` environment variable wins; it
is a URL to the index document, a ``file://`` URL, or a local directory
path. Otherwise ``~/.niko/config.toml`` is consulted::

    [registry]
    url = "https://example.com/niko/index.json"

Network rule (extends the Alpha 16 guarantee): only ``niko2 get`` and
``niko2 publish`` -- i.e. ``install_from_registry``, ``update_package``
and ``publish_package`` below -- ever touch the network. Compiling,
running, checking, the LSP and the debugger resolve packages from the
local cache only.
"""

import hashlib
import json
import os
import shutil
import socket
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import semver
from .packages import (
    InstallResult,
    PackageError,
    cache_dir_for,
    default_cache_root,
    find_lock_dir,
    installed_versions,
    parse_manifest,
    pin_closure_under,
    write_package_pin,
)

#: Environment variable selecting the registry (index URL, file:// URL,
#: or local directory path). Wins over the config file.
REGISTRY_ENV_VAR = 'NIKO_REGISTRY'

#: Config file fallback: ~/.niko/config.toml  with  [registry] url = ...
CONFIG_FILE = Path.home() / '.niko' / 'config.toml'

#: Name of the index document inside a local directory registry.
INDEX_FILENAME = 'index.json'

#: Subdirectory of a local registry holding published tarballs.
TARBALL_DIRNAME = 'tarballs'

#: Network timeout for remote registry fetches (seconds): the index
#: fetch and every tarball download. Short enough that a dead host fails
#: fast instead of hanging scripts and tests; ``urlopen``'s timeout
#: covers both connect and read. (Alpha 24: was 30s -- too long for an
#: unreachable host and for hermetic test fixtures.)
_FETCH_TIMEOUT = 10


@dataclass
class Registry:
    """A resolved registry reference."""
    kind: str        # 'dir' | 'remote'
    spec: str        # the reference as configured (for messages + source records)
    dir: Path | None = None        # local directory (kind == 'dir')
    index_url: str | None = None   # index document URL (kind == 'remote')


@dataclass
class UpdateResult:
    """Outcome of ``update_package``."""
    name: str
    old_version: str
    new_version: str
    path: Path
    changed: bool     # False when already at the newest matching version


# ---------------------------------------------------------------------------
# Registry selection: NIKO_REGISTRY env var, else ~/.niko/config.toml
# ---------------------------------------------------------------------------

def _config_registry_url():
    """The ``[registry] url`` from ~/.niko/config.toml, or None."""
    try:
        text = CONFIG_FILE.read_text(encoding='utf8')
    except OSError:
        return None
    try:
        import tomllib
    except ImportError:
        return None
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise PackageError(f'cannot read {CONFIG_FILE}: bad TOML: {e}')
    if not isinstance(data, dict):
        return None
    reg = data.get('registry')
    if not isinstance(reg, dict):
        return None
    url = reg.get('url')
    if url is None:
        return None
    if not isinstance(url, str) or not url.strip():
        raise PackageError(
            f'{CONFIG_FILE}: [registry] "url" must be text, e.g. '
            'url = "https://example.com/niko/index.json"')
    return url.strip()


def resolve_registry(ref=None):
    """Resolve *ref* (or env/config) to a ``Registry``.

    Raises ``PackageError`` when no registry is configured.
    """
    raw = (ref or os.environ.get(REGISTRY_ENV_VAR, '')
           or _config_registry_url() or '').strip()
    if not raw:
        raise PackageError(
            'no registry configured -- set the NIKO_REGISTRY environment '
            'variable to a registry index URL or a local registry directory '
            f'(or add [registry] url = "..." to {CONFIG_FILE})')
    if raw.startswith('file://'):
        path = urllib.parse.unquote(urllib.parse.urlparse(raw).path)
        return Registry(kind='dir', spec=raw,
                        dir=Path(path).expanduser())
    if '://' in raw:
        scheme = urllib.parse.urlparse(raw).scheme.lower()
        if scheme not in ('http', 'https'):
            raise PackageError(
                f'bad registry "{raw}" -- use an http(s):// index URL, a '
                'file:// URL, or a local directory path')
        return Registry(kind='remote', spec=raw, index_url=raw)
    return Registry(kind='dir', spec=raw, dir=Path(raw).expanduser())


# ---------------------------------------------------------------------------
# Index fetching (network only happens here and in _download)
# ---------------------------------------------------------------------------

def _read_local_index(reg):
    index_path = reg.dir / INDEX_FILENAME
    if not index_path.is_file():
        raise PackageError(
            f'registry directory {reg.dir} has no {INDEX_FILENAME} -- not a '
            'Niko registry? (publish with '
            '`niko2 publish --registry <dir>` to create one)')
    try:
        data = json.loads(index_path.read_text(encoding='utf8'))
    except (OSError, ValueError) as e:
        raise PackageError(f'cannot read registry index {index_path}: {e}')
    return data


def _read_remote_index(reg):
    # urllib follows GET redirects by default; a redirect to something
    # that is not a JSON index still fails below with a clear message.
    try:
        with urllib.request.urlopen(reg.index_url,
                                    timeout=_FETCH_TIMEOUT) as resp:
            raw = resp.read().decode('utf8')
    except Exception as e:
        raise PackageError(
            _network_error('registry index', reg.index_url, e))
    try:
        return json.loads(raw)
    except ValueError as e:
        raise PackageError(
            f'registry index at {reg.index_url} is not valid JSON: {e}')


def _network_error(what, url, e):
    """Plain-English one-liner for a failed network fetch.

    *what* names the artifact ('registry index', 'tarball for "x"
    1.0.0'); *url* is the attempted URL. Distinguishes the common
    failure modes -- DNS failure, connection refused, timeout, HTTP
    error status -- instead of surfacing urllib's raw exception text.
    """
    host = urllib.parse.urlparse(url).hostname or url
    if isinstance(e, urllib.error.HTTPError):
        reason = f' {e.reason}' if e.reason else ''
        return (f'could not fetch {what} from {url}: the server replied '
                f'with HTTP {e.code}{reason}')
    cause = e.reason if isinstance(e, urllib.error.URLError) else e
    if isinstance(cause, socket.gaierror):
        return (f'could not fetch {what} from {url}: could not resolve '
                f'"{host}" (DNS failure) -- check the registry address')
    if isinstance(cause, ConnectionRefusedError):
        return (f'could not fetch {what} from {url}: connection refused '
                f'-- is anything serving the registry at {host}?')
    if isinstance(cause, TimeoutError):
        # socket.timeout is an alias of TimeoutError since Python 3.10.
        return (f'could not fetch {what} from {url}: timed out after '
                f'{_FETCH_TIMEOUT} seconds -- the registry may be down '
                'or unreachable')
    return f'could not fetch {what} from {url}: {e}'


def fetch_index(reg):
    """Fetch and minimally validate a registry's index document."""
    data = _read_remote_index(reg) if reg.kind == 'remote' \
        else _read_local_index(reg)
    if not isinstance(data, dict) or \
            not isinstance(data.get('packages'), dict):
        where = reg.index_url if reg.kind == 'remote' \
            else str(reg.dir / INDEX_FILENAME)
        raise PackageError(
            f'registry index at {where} is corrupt: expected a JSON object '
            'with a "packages" table')
    return data


def registry_versions(index, name):
    """Sorted version strings listed for *name* in the index ([])."""
    entries = (index.get('packages') or {}).get(name)
    if not entries:
        return []
    if not isinstance(entries, dict):
        raise PackageError(
            f'registry index: entry for package "{name}" is corrupt '
            '(expected a version table)')
    out = []
    for v in entries:
        try:
            semver.parse_version(v)
        except semver.SemverError:
            raise PackageError(
                f'registry index: bad version key "{v}" for package "{name}"')
        out.append(v)
    return sorted(out, key=semver.parse_version)


def _entry_for(index, name, version):
    entries = (index.get('packages') or {}).get(name) or {}
    entry = entries.get(version)
    if not isinstance(entry, dict):
        raise PackageError(
            f'registry index: entry for "{name}" version {version} is corrupt')
    url = entry.get('url')
    sha256 = entry.get('sha256')
    if not url or not isinstance(url, str):
        raise PackageError(
            f'registry index: "{name}" {version} has no tarball "url"')
    if not sha256 or not isinstance(sha256, str):
        raise PackageError(
            f'registry index: "{name}" {version} has no "sha256" -- '
            'refusing to install without an integrity hash')
    return entry


def _tarball_url(reg, entry_url):
    """Resolve an index entry's ``url`` against the registry."""
    entry_url = entry_url.strip()
    if '://' in entry_url or entry_url.startswith('file://'):
        return entry_url
    if reg.kind == 'remote':
        return urllib.parse.urljoin(reg.index_url, entry_url)
    target = (reg.dir / entry_url)
    # Keep tarball references inside the registry directory.
    resolved = target.resolve() if target.exists() else target.absolute()
    root = reg.dir.resolve() if reg.dir.exists() else reg.dir.absolute()
    if resolved != root and root not in resolved.parents:
        raise PackageError(
            f'registry index: tarball url "{entry_url}" escapes the '
            f'registry directory {reg.dir}')
    return 'file://' + resolved.as_posix()


def _download(url, dest, what='file'):
    """Fetch *url* (http(s) or file) to *dest*. Network only here.

    The file:// branch is a plain local copy -- it never goes through
    the network stack, so it never needs the fetch timeout. *what*
    names the artifact for error messages.
    """
    if url.startswith('file://'):
        src = Path(urllib.parse.unquote(urllib.parse.urlparse(url).path))
        if not src.is_file():
            raise PackageError(f'registry tarball not found: {src}')
        shutil.copyfile(src, dest)
        return
    try:
        with urllib.request.urlopen(url, timeout=_FETCH_TIMEOUT) as resp, \
                open(dest, 'wb') as f:
            shutil.copyfileobj(resp, f, length=1024 * 64)
    except PackageError:
        raise
    except Exception as e:
        raise PackageError(_network_error(what, url, e))


def _download_and_verify(reg, name, version, entry):
    """Download the tarball, verify sha256, return the temp file path.

    A hash mismatch is a hard error and the bad download is deleted.
    """
    url = _tarball_url(reg, entry['url'])
    tmp = Path(tempfile.mkdtemp(prefix='niko-reg-'))
    tgz = tmp / f'{name}-{version}.tar.gz'
    try:
        _download(url, tgz, f'tarball for "{name}" {version}')
        digest = hashlib.sha256(tgz.read_bytes()).hexdigest()
        if digest != entry['sha256'].strip().lower():
            raise PackageError(
                f'sha256 mismatch for "{name}" {version} from the registry '
                f'({reg.spec}): expected {entry["sha256"].strip()[:16]}..., '
                f'got {digest[:16]}... -- the registry copy may be corrupt '
                'or tampered with (deleted the bad download)')
        return tgz, tmp
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def _safe_extract(tgz, dest):
    """Extract a tarball, rejecting absolute paths and ``..`` escapes."""
    dest = Path(dest)
    with tarfile.open(tgz, 'r:gz') as tf:
        for member in tf.getmembers():
            p = Path(member.name)
            if p.is_absolute() or '..' in p.parts:
                raise PackageError(
                    f'registry tarball has an unsafe path "{member.name}" -- '
                    'refusing to extract')
        tf.extractall(dest)


# ---------------------------------------------------------------------------
# Install from a registry: `niko2 get <name>[@<range>]`
# ---------------------------------------------------------------------------

def _write_registry_source_record(pkg_dir, reg, range_spec):
    (Path(pkg_dir) / '.niko-source.json').write_text(
        json.dumps({'source': f'registry:{reg.spec}', 'kind': 'registry',
                    'range': range_spec}, indent=2) + '\n',
        encoding='utf8')


def _ensure_dependencies(manifest, reg, cache_root):
    """Install a package's ``[dependencies]`` (name -> range).

    Full transitive closure: each dependency's own dependencies are
    installed recursively from the *same* registry. A dependency that
    already has a satisfying version in the cache is left alone (no
    network, no reinstall).

    Termination: recursion happens only when *no* cached version
    satisfies the range, and the recursive install then caches the
    chosen (name, version) -- which was not cached before. Every
    recursion level strictly grows the cache, and a registry lists
    finitely many versions, so this cannot loop forever, even when
    manifests form a dependency cycle (a<->b just installs both; an
    actual *module* import cycle is still caught by modules.py with a
    proper error). Conflicting ranges may install two versions of one
    package (e.g. b 1.x for a, b 2.x for c) -- same tradeoff npm makes.

    Note: version pins are a project-level concept (``niko.lock`` is
    found by walking up from the importing file). A ``pkg:`` import
    *inside* a cached package therefore resolves to the newest cached
    version, not the project's pin -- pre-existing Alpha 16 behavior,
    unchanged here. ``get`` guarantees a range-satisfying version is
    cached, so nested imports always resolve.
    """
    for dep_name, dep_range in manifest.dependencies.items():
        satisfied = any(
            semver.satisfies(v, dep_range)
            for _, v, _ in installed_versions(dep_name, cache_root))
        if satisfied:
            continue
        install_from_registry(dep_name, dep_range, cache_root=cache_root,
                              force=False, registry=reg.spec)


def install_from_registry(name, range_spec='*', *, cache_root=None,
                          force=False, registry=None):
    """Install the max version of *name* satisfying *range_spec*.

    Resolves the registry (argument, ``NIKO_REGISTRY``, or config file),
    picks the highest listed version matching the range, downloads the
    tarball, verifies its sha256, extracts it into the cache, and
    installs ``[dependencies]`` recursively. Returns ``InstallResult``;
    idempotent like ``install_package`` (already cached -> ``fresh=False``,
    ``force=True`` reinstalls).

    Raises ``PackageError`` (plain English) when no registry is
    configured, the package is unknown, no version satisfies the range,
    the download fails integrity, or the tarball disagrees with the
    index. Network happens only here (and ``publish``/``update``).
    """
    rng = semver.parse_range(range_spec)  # validates early, clear errors
    reg = resolve_registry(registry)
    index = fetch_index(reg)
    versions = registry_versions(index, name)
    if not versions:
        raise PackageError(
            f'package "{name}" not found in registry {reg.spec}')
    best = semver.max_satisfying(versions, range_spec)
    if best is None:
        have = ', '.join(versions)
        raise PackageError(
            f'no version of "{name}" satisfies "{range_spec}" '
            f'(registry {reg.spec} has: {have})')
    root = cache_root or default_cache_root()
    dest = cache_dir_for(name, best, root)

    if dest.is_dir() and not force:
        manifest = parse_manifest(dest / 'niko.toml')
        _ensure_dependencies(manifest, reg, root)
        return InstallResult(manifest, dest, fresh=False)

    entry = _entry_for(index, name, best)
    tgz, tmp = _download_and_verify(reg, name, best, entry)
    # No partial installs: the cache directory `dest` is only touched
    # AFTER the tarball is downloaded AND its sha256 verified AND its
    # manifest/entry checks pass -- a failed download, a hash mismatch,
    # or a bad tarball can never leave a half-installed package behind
    # (the temp dir is always removed; `dest` is replaced only here).
    try:
        stage = tmp / 'stage'
        stage.mkdir()
        _safe_extract(tgz, stage)
        manifest_path = stage / 'niko.toml'
        if not manifest_path.is_file():
            raise PackageError(
                f'registry tarball for "{name}" {best} has no niko.toml')
        manifest = parse_manifest(manifest_path)
        if manifest.name != name or manifest.version != best:
            raise PackageError(
                f'registry tarball for "{name}" {best} disagrees with the '
                f'index: it contains {manifest.name} {manifest.version}')
        entry_file = stage / manifest.entry
        if not entry_file.is_file():
            raise PackageError(
                f'registry tarball for "{name}" {best}: entry '
                f'"{manifest.entry}" not found')
        if dest.is_dir():
            shutil.rmtree(dest)
        shutil.copytree(stage, dest,
                        ignore=shutil.ignore_patterns('.niko-source.json'))
        _write_registry_source_record(dest, reg, rng.text)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    _ensure_dependencies(manifest, reg, root)
    return InstallResult(manifest, dest, fresh=True)


# ---------------------------------------------------------------------------
# `niko2 get <arg>` disambiguation: registry spec vs dir/git source
# ---------------------------------------------------------------------------

def is_registry_spec(arg):
    """Is this ``niko2 get`` argument a registry lookup?

    Disambiguation rule: ``<name>`` or ``<name>@<range>`` where ``<name>``
    matches the package-name regex (letters/digits/``-``/``_``, starting
    with a letter -- so it can contain no ``/``, ``.`` or URL scheme by
    construction) and, when a range is present, the range parses as a
    version range and contains no ``/`` or URL scheme. Anything else
    falls through to the Alpha 16 directory/git-URL handling::

        "greet"            registry lookup
        "greet@^1.0"       registry lookup, range ^1.0
        "greet@"           NOT registry (empty range -> clear error later)
        "./greet"          local directory (name part "./greet" invalid)
        "greet.git"        git URL (name part "greet.git" invalid)
        "https://x/y.git"  git URL (name part "https:" invalid)
        "git@h:x/y.git"    git URL (range part "h:x/y.git" has a /)
        "file:///x"        git URL (name part "file:" invalid)

    Consequence: a bare local directory whose name is exactly a valid
    package name (``niko2 get greet`` with a local ./greet/) is treated
    as a registry lookup -- spell it ``./greet`` to force directory
    handling.
    """
    from .packages import _NAME_RE
    s = (arg or '').strip()
    if not s:
        return False
    name, at, range_part = s.partition('@')
    if not _NAME_RE.match(name):
        return False
    if not at:
        return True
    # A range is present: it must parse, and must not smuggle a path/URL.
    if '/' in range_part or '\\' in range_part or '://' in range_part:
        return False
    try:
        semver.parse_range(range_part)
    except semver.SemverError:
        return False
    return True


def split_registry_spec(arg):
    """``"name@range"`` -> ``(name, range)``; ``"name"`` -> ``(name, '*')``.

    Raises ``PackageError`` for an empty range or an unparseable one.
    """
    s = (arg or '').strip()
    name, at, range_part = s.partition('@')
    name = name.strip()
    if at and not range_part.strip():
        raise PackageError(
            f'niko2 get "{s}": empty version range after "@" -- '
            'try e.g. "niko2 get {0}@^1.0"'.format(name))
    range_spec = range_part.strip() or '*'
    try:
        semver.parse_range(range_spec)
    except semver.SemverError as e:
        raise PackageError(f'niko2 get "{s}": {e}')
    return name, range_spec


# ---------------------------------------------------------------------------
# Publish: `niko2 publish [--registry ...]`
# ---------------------------------------------------------------------------

def _build_tarball(pkg_dir):
    """Gzip tarball of *pkg_dir*'s contents (top-level), minus the
    standard excludes: ``.git/``, ``__pycache__/``, ``*.pyc``."""
    tmp = Path(tempfile.mkdtemp(prefix='niko-publish-'))
    tgz = tmp / 'package.tar.gz'

    def wanted(p):
        rel = p.relative_to(pkg_dir)
        if p.suffix == '.pyc':
            return False
        return not any(part in ('.git', '__pycache__') for part in rel.parts)

    with tarfile.open(tgz, 'w:gz') as tf:
        for p in sorted(pkg_dir.rglob('*')):
            if not wanted(p):
                continue
            tf.add(p, arcname=p.relative_to(pkg_dir).as_posix(),
                   recursive=False)
    return tgz, tmp


def _load_index_for_write(reg):
    index_path = reg.dir / INDEX_FILENAME
    if not index_path.is_file():
        return {'packages': {}}
    try:
        data = json.loads(index_path.read_text(encoding='utf8'))
    except (OSError, ValueError) as e:
        raise PackageError(
            f'cannot read registry index {index_path}: {e}')
    if not isinstance(data, dict) or not isinstance(data.get('packages'), dict):
        raise PackageError(
            f'registry index {index_path} is corrupt: expected a JSON '
            'object with a "packages" table')
    return data


def publish_package(pkg_dir='.', *, registry=None, force=False):
    """Publish the package in *pkg_dir* to a registry.

    The directory must contain a valid ``niko.toml``. A tarball of the
    package is built (excluding ``.git/``, ``__pycache__/``, ``*.pyc``),
    its sha256 recorded, and -- for a local directory registry -- the
    tarball is copied into place and the index updated atomically
    (temp file + rename). Publishing a version that already exists is
    refused unless ``force`` is given.

    Remote (http/https) registries have no auth story yet: publishing to
    one is a clear error. Returns ``{'name', 'version', 'spec'}``.
    """
    pkg_dir = Path(pkg_dir)
    manifest = parse_manifest(pkg_dir / 'niko.toml')
    entry_file = pkg_dir / manifest.entry
    if not entry_file.is_file():
        raise PackageError(
            f'{manifest.manifest_path}: entry "{manifest.entry}" not found '
            f'in the package directory {pkg_dir}')
    reg = resolve_registry(registry)
    if reg.kind == 'remote':
        raise PackageError(
            'cannot publish to a remote registry -- publishing needs auth, '
            'which is not supported yet; use a local directory registry '
            '(NIKO_REGISTRY=/path/to/dir or '
            '`niko2 publish --registry /path/to/dir`)')

    tgz, tmp = _build_tarball(pkg_dir)
    try:
        sha256 = hashlib.sha256(tgz.read_bytes()).hexdigest()
        index = _load_index_for_write(reg)
        versions = index['packages'].setdefault(manifest.name, {})
        if manifest.version in versions and not force:
            raise PackageError(
                f'version {manifest.version} of "{manifest.name}" is '
                f'already published to {reg.spec} -- use --force to overwrite')
        tardir = reg.dir / TARBALL_DIRNAME
        tardir.mkdir(parents=True, exist_ok=True)
        tarball_name = f'{manifest.name}-{manifest.version}.tar.gz'
        shutil.copyfile(tgz, tardir / tarball_name)
        versions[manifest.version] = {
            'url': f'{TARBALL_DIRNAME}/{tarball_name}',
            'sha256': sha256,
            'description': manifest.description,
            'dependencies': dict(manifest.dependencies),
        }
        # Atomic index update: write temp, then rename.
        index_path = reg.dir / INDEX_FILENAME
        tmp_index = index_path.with_name(INDEX_FILENAME + '.tmp')
        tmp_index.write_text(
            json.dumps(index, indent=2, sort_keys=True) + '\n',
            encoding='utf8')
        os.replace(tmp_index, index_path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {'name': manifest.name, 'version': manifest.version,
            'spec': reg.spec}


# ---------------------------------------------------------------------------
# Update: `niko2 get --update <name>`
# ---------------------------------------------------------------------------

def update_package(name, *, cwd=None, cache_root=None):
    """Re-resolve *name* from its registry and upgrade the install + pin.

    The range comes from the lockfile's ``packages`` entry (``range``
    field), falling back to the install's recorded range, else ``*``.
    The newest satisfying version is installed (from the same registry
    the package came from -- ``source: "registry:<spec>"``) and the
    lockfile pin is rewritten to the new version, keeping the range.

    Only registry-installed packages can be updated; anything else is a
    plain-English error. Returns ``UpdateResult``.
    """
    name = (name or '').strip()
    if not name:
        raise PackageError('niko2 get --update needs a package name')
    if '@' in name:
        plain = name.split('@')[0].strip() or name
        raise PackageError(
            f'niko2 get --update takes a plain package name, not "{name}" -- '
            f'to change the range, run `niko2 get {plain}@<range>` instead')
    start = Path(cwd or os.getcwd())
    lock_dir = find_lock_dir(start)
    if lock_dir is None:
        raise PackageError(
            f'no niko.lock found above {start} -- run '
            f'`niko2 get {name}@<range>` inside a project first')
    lock_path = lock_dir / 'niko.lock'
    try:
        data = json.loads(lock_path.read_text(encoding='utf8'))
    except (OSError, ValueError) as e:
        raise PackageError(f'cannot read {lock_path}: {e}')
    entry = (data.get('packages') or {}).get(name)
    if not isinstance(entry, dict):
        raise PackageError(
            f'package "{name}" is not pinned in {lock_path} -- run '
            f'`niko2 get {name}@<range>` first')
    source = entry.get('source') or ''
    if not source.startswith('registry:'):
        raise PackageError(
            f'package "{name}" was installed from "{source or "unknown"}" -- '
            '`niko2 get --update` only upgrades registry packages')
    reg_spec = source[len('registry:'):]
    range_spec = entry.get('range') or '*'

    reg = resolve_registry(reg_spec)
    index = fetch_index(reg)
    versions = registry_versions(index, name)
    best = semver.max_satisfying(versions, range_spec)
    if best is None:
        have = ', '.join(versions) if versions else '(none)'
        raise PackageError(
            f'no version of "{name}" satisfies "{range_spec}" '
            f'(registry {reg.spec} has: {have})')
    old_version = entry.get('version')
    if best == old_version:
        path = cache_dir_for(name, best, cache_root)
        return UpdateResult(name, old_version, best, path, changed=False)
    result = install_from_registry(name, range_spec, cache_root=cache_root,
                                   force=True, registry=reg_spec)
    write_package_pin(lock_dir, name, result.manifest.version,
                      f'registry:{reg_spec}', range_spec)
    # Alpha 24: re-resolve the closure beneath the updated package and
    # re-pin its subtree -- the new version's [dependencies] may ask for
    # different ranges than the old one did. Same resolution rule as
    # `niko2 lock` (see packages._transitive_closure_pins); pins outside
    # this subtree are untouched, and stale entries are left for the next
    # `niko2 lock` to drop.
    pin_closure_under(lock_dir, name, cache_root=cache_root)
    return UpdateResult(name, old_version, result.manifest.version,
                        result.path, changed=True)


# ---------------------------------------------------------------------------
# Locked reinstall: `niko2 get` on a fresh cache (Alpha 24)
# ---------------------------------------------------------------------------

def ensure_locked_closure_installed(lock_dir, skip=(), cache_root=None):
    """Install lockfile-pinned package versions missing from the cache.

    ``niko2 get <name>`` installs the top-level package plus
    range-satisfying dependency versions, but on a fresh machine the
    *exact* transitive versions pinned by ``niko2 lock`` are missing --
    the installer may even have picked a *newer* satisfying version
    than the pin. This walks the lockfile's ``"packages"`` table and
    installs each pinned version that isn't cached yet, exactly (the
    version string itself is the range), from the registry recorded in
    the pin's own ``source`` field.

    Only additive: nothing is ever downgraded, replaced, or removed --
    a version that is already cached (pinned or otherwise) is left
    alone. A pin whose version is no longer listed by its registry is a
    clear error. Non-registry pins (local directory / git sources) are
    skipped -- their installed version is fixed by the source itself,
    so there is no exact remote version to fetch. *skip* names packages
    to leave alone (the caller just installed them). Returns the
    ``[(name, version)]`` pairs actually installed.
    """
    lock_path = Path(lock_dir) / 'niko.lock'
    try:
        data = json.loads(lock_path.read_text(encoding='utf8')) \
            if lock_path.is_file() else {}
    except ValueError as e:
        raise PackageError(f'cannot read {lock_path}: {e}')
    pkgs = data.get('packages')
    if not isinstance(pkgs, dict):
        return []
    installed = []
    for pname, entry in sorted(pkgs.items()):
        if pname in skip or not isinstance(entry, dict):
            continue
        version = entry.get('version')
        source = entry.get('source') or ''
        if not version:
            continue
        if cache_dir_for(pname, version, cache_root).is_dir():
            continue
        if not source.startswith('registry:'):
            # Local/git pins: the source is authoritative for the
            # version, so a "missing exact version" cannot happen --
            # nothing to fetch, nothing to do.
            continue
        install_from_registry(pname, version, cache_root=cache_root,
                              force=False,
                              registry=source[len('registry:'):])
        installed.append((pname, version))
    return installed
