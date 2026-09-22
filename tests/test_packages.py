#!/usr/bin/env python3
"""Alpha 16: package manager tests -- `niko2 get`, `pkg:` imports, lockfile.

Hermetic by construction: no network (git coverage uses a local `file://`
fixture repo), no writes outside tmp dirs, and NIKO_PKG_CACHE is pointed at
a throwaway dir for the whole run -- the real ~/.niko is never touched.

Run:  python3 tests/test_packages.py
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

root = pathlib.Path(__file__).resolve().parent
project_root = root.parent
sys.path.insert(0, str(project_root))

# One throwaway cache for the whole script. Every subprocess below inherits
# os.environ, so install + resolution can never reach the real ~/.niko.
SESSION = pathlib.Path(tempfile.mkdtemp(prefix='niko-pkgtest-'))
os.environ['NIKO_PKG_CACHE'] = str(SESSION / 'cache')

from niko2.packages import (  # noqa: E402
    PackageError,
    parse_manifest,
    install_package,
    resolve_package,
    installed_versions,
    read_source_record,
    default_cache_root,
    locked_package_versions,
    collect_package_imports,
    lock_packages_for_project,
)


def _niko2(*args, cwd, env=None):
    e = dict(os.environ)
    e['PYTHONPATH'] = str(project_root)
    if env:
        e.update(env)
    return subprocess.run([sys.executable, '-m', 'niko2', *args],
                          capture_output=True, text=True, cwd=cwd, env=e)


def _strip_banner(out):
    lines = out.splitlines(keepends=True)
    if lines and lines[0].startswith('\u2713 built'):
        lines = lines[1:]
    return ''.join(lines)


def _write_pkg(src_dir, name, version, files, entry='main.niko', manifest=None):
    """Write a package fixture dir: niko.toml + .niko files. Returns dir."""
    src_dir = pathlib.Path(src_dir)
    src_dir.mkdir(parents=True, exist_ok=True)
    if manifest is None:
        manifest = (f'[package]\nname = "{name}"\nversion = "{version}"\n'
                    f'entry = "{entry}"\n')
    (src_dir / 'niko.toml').write_text(manifest, encoding='utf8')
    for rel, content in files.items():
        p = src_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding='utf8')
    return src_dir


def _expect_pkg_error(fn, *needles):
    """fn() must raise PackageError whose text contains every needle."""
    try:
        fn()
    except PackageError as e:
        msg = str(e)
        for n in needles:
            assert n in msg, f'error text {msg!r} missing {n!r}'
        return msg
    raise AssertionError(f'expected PackageError, got success from {fn}')


# -- 1. manifest validation -------------------------------------------------
mcase = pathlib.Path(tempfile.mkdtemp(prefix='niko-pkg-manifest-'))


def _manifest(name, text):
    p = mcase / name
    p.write_text(text, encoding='utf8')
    return p


# bad name: starts with a digit
p = _manifest('badname1.toml', '[package]\nname = "1abc"\nversion = "1.0.0"\n')
_expect_pkg_error(lambda: parse_manifest(p), str(p), 'bad package name', '"1abc"')
# bad name: spaces
p = _manifest('badname2.toml', '[package]\nname = "my pkg"\nversion = "1.0.0"\n')
_expect_pkg_error(lambda: parse_manifest(p), str(p), 'bad package name', '"my pkg"')
# bad version: not X.Y.Z
p = _manifest('badver1.toml', '[package]\nname = "ok"\nversion = "1.2"\n')
_expect_pkg_error(lambda: parse_manifest(p), str(p), 'bad version', '"1.2"')
# bad version: leading zeros
p = _manifest('badver2.toml', '[package]\nname = "ok"\nversion = "01.2.3"\n')
_expect_pkg_error(lambda: parse_manifest(p), str(p), 'bad version', '"01.2.3"')
# bad version: four components
p = _manifest('badver3.toml', '[package]\nname = "ok"\nversion = "1.2.3.4"\n')
_expect_pkg_error(lambda: parse_manifest(p), str(p), 'bad version', '"1.2.3.4"')
# missing name
p = _manifest('noname.toml', '[package]\nversion = "1.0.0"\n')
_expect_pkg_error(lambda: parse_manifest(p), str(p), 'missing required field "name"')
# missing version
p = _manifest('nover.toml', '[package]\nname = "ok"\n')
_expect_pkg_error(lambda: parse_manifest(p), str(p), 'missing required field "version"')
# non-string name
p = _manifest('typename.toml', '[package]\nname = 42\nversion = "1.0.0"\n')
_expect_pkg_error(lambda: parse_manifest(p), str(p), '"name" must be text')
# entry must be a .niko file
p = _manifest('badentry.toml',
              '[package]\nname = "ok"\nversion = "1.0.0"\nentry = "main.txt"\n')
_expect_pkg_error(lambda: parse_manifest(p), str(p), '"entry" must name a .niko file')
# missing manifest file entirely
missing = mcase / 'does-not-exist' / 'niko.toml'
_expect_pkg_error(lambda: parse_manifest(missing), str(missing), 'cannot read')
# TOML syntax error
p = _manifest('badtoml.toml', '[package]\nname = [broken\n')
_expect_pkg_error(lambda: parse_manifest(p), str(p), 'bad TOML')

# a valid manifest parses, with defaults for entry/description
p = _manifest('good.toml', '[package]\nname = "acme-utils"\nversion = "2.10.3"\n')
m = parse_manifest(p)
assert (m.name, m.version, m.entry, m.description) == \
    ('acme-utils', '2.10.3', 'main.niko', ''), m
# Alpha 4-era flat manifests (no [package] table) still work
p = _manifest('flat.toml', 'name = "flat-pkg"\nversion = "0.1.0"\n')
m = parse_manifest(p)
assert (m.name, m.version) == ('flat-pkg', '0.1.0'), m
print('ok: manifest validation')

# install-level validation: missing entry file, non-dir and missing sources
msrc = pathlib.Path(tempfile.mkdtemp(prefix='niko-pkg-install-err-'))
_write_pkg(msrc / 'noentry', 'noentry', '1.0.0', {'other.niko': 'say 1\n'})
_expect_pkg_error(lambda: install_package(str(msrc / 'noentry')),
                  'entry "main.niko" not found', 'noentry')
afile = msrc / 'afile.txt'
afile.write_text('hello', encoding='utf8')
_expect_pkg_error(lambda: install_package(str(afile)),
                  str(afile), 'is not a directory')
_expect_pkg_error(lambda: install_package(str(msrc / 'ghost-dir')),
                  'is not a directory and not a git URL')
_expect_pkg_error(lambda: install_package('   '), 'niko2 get needs a source')
print('ok: install validation')

# -- 2. local-dir install ----------------------------------------------------
tsrc = pathlib.Path(tempfile.mkdtemp(prefix='niko-pkg-src-'))
pkg_src = _write_pkg(tsrc / 'greet', 'greet', '1.2.0', {
    'main.niko': 'to hello with who:\n    give back "hi " + who\n'
                 'set tag to "greet-1.2.0"\n',
    'helpers.niko': 'to loud with s:\n    give back upper(s)\n',
})
r = install_package(str(pkg_src))
assert r.fresh is True, r
assert r.manifest.name == 'greet' and r.manifest.version == '1.2.0'
cache_dir = SESSION / 'cache' / 'greet-1.2.0'
assert r.path == cache_dir and cache_dir.is_dir()
assert (cache_dir / 'main.niko').is_file()
assert (cache_dir / 'helpers.niko').is_file()
assert (cache_dir / 'niko.toml').is_file()
rec = read_source_record(cache_dir)
assert rec.get('kind') == 'local', rec
assert rec.get('source') == str(pkg_src.resolve()), rec
assert read_source_record(SESSION / 'cache' / 'no-such-pkg') == {}
# newest/oldest ordering helper sees it
vers = installed_versions('greet')
assert [(v, p) for _, v, p in vers] == [('1.2.0', cache_dir)], vers
assert installed_versions('no-such-pkg') == []
assert resolve_package('greet') == cache_dir

# CLI: fresh install of a second fixture package
pkg2_src = _write_pkg(tsrc / 'local2', 'local2', '0.1.0',
                      {'main.niko': 'say "local2"\n'})
proj = pathlib.Path(tempfile.mkdtemp(prefix='niko-pkg-proj-'))
cli = _niko2('get', str(pkg2_src), cwd=proj)
assert cli.returncode == 0, cli.stderr or cli.stdout
assert '\u2713 installed local2 0.1.0' in cli.stdout, cli.stdout
assert (SESSION / 'cache' / 'local2-0.1.0' / 'main.niko').is_file()
print('ok: local-dir install')

# -- 3. idempotence + --force ------------------------------------------------
r2 = install_package(str(pkg_src))
assert r2.fresh is False, r2
assert r2.path == cache_dir
cli = _niko2('get', str(pkg_src), cwd=proj)
assert cli.returncode == 0, cli.stderr or cli.stdout
assert 'already installed' in cli.stdout, cli.stdout
assert '--force' in cli.stdout, cli.stdout

# --force reinstalls: tamper with the cache, update the source, reinstall
(cache_dir / 'main.niko').write_text('say "TAMPERED"\n', encoding='utf8')
(pkg_src / 'main.niko').write_text(
    'to hello with who:\n    give back "yo " + who\nset tag to "greet-1.2.0"\n',
    encoding='utf8')
cli = _niko2('get', str(pkg_src), '--force', cwd=proj)
assert cli.returncode == 0, cli.stderr or cli.stdout
assert '\u2713 installed greet 1.2.0' in cli.stdout, cli.stdout
got = (cache_dir / 'main.niko').read_text(encoding='utf8')
assert 'give back "yo " + who' in got, got
# in-process force path agrees
r3 = install_package(str(pkg_src), force=True)
assert r3.fresh is True
print('ok: idempotence + --force')

# -- 4. file:// git install (hermetic) ---------------------------------------
git = shutil.which('git')
if git is None:
    print('SKIP git install: git not on PATH')
else:
    grepo = pathlib.Path(tempfile.mkdtemp(prefix='niko-pkg-gitrepo-'))
    _write_pkg(grepo, 'gitlib', '0.3.1',
               {'main.niko': 'to triple with x:\n    give back x * 3\n'})
    subprocess.run([git, 'init', '-q'], cwd=grepo, check=True)
    subprocess.run([git, 'add', '-A'], cwd=grepo, check=True)
    subprocess.run([git, '-c', 'user.email=t@t', '-c', 'user.name=t',
                    'commit', '-qm', 'init'], cwd=grepo, check=True)
    url = grepo.as_uri()  # file:// URL, no network involved
    assert url.startswith('file://'), url
    cli = _niko2('get', url, cwd=proj)
    assert cli.returncode == 0, cli.stderr or cli.stdout
    assert '\u2713 installed gitlib 0.3.1' in cli.stdout, cli.stdout
    gdir = SESSION / 'cache' / 'gitlib-0.3.1'
    assert (gdir / 'main.niko').is_file()
    assert not (gdir / '.git').exists(), '.git metadata must be stripped'
    grec = read_source_record(gdir)
    assert grec.get('kind') == 'git', grec
    assert grec.get('source') == url, grec
    print('ok: file:// git install')

# missing git binary -> clear error, no clone attempted
_real_which = shutil.which
shutil.which = lambda *a, **k: None
try:
    _expect_pkg_error(lambda: install_package('file:///tmp/whatever-pkg'),
                      'git is not installed')
finally:
    shutil.which = _real_which
print('ok: git-missing error')

# -- 5. end-to-end import (VM; WASM when node exists) -------------------------
e2e_src = pathlib.Path(tempfile.mkdtemp(prefix='niko-pkg-e2e-src-'))
_write_pkg(e2e_src / 'shapes', 'shapes', '1.0.0', {
    'helpers.niko': 'to exclaim with s:\n    give back s + "!"\n',
    'main.niko': 'import "./helpers.niko" as h\n'
                 'to shout with s:\n'
                 '    give back h.exclaim(upper(s))\n'
                 'set label to "shapes-lib"\n',
})
install_package(str(e2e_src / 'shapes'))
e2e_proj = pathlib.Path(tempfile.mkdtemp(prefix='niko-pkg-e2e-proj-'))
(e2e_proj / 'app.niko').write_text(
    'import "pkg:shapes/main.niko" as sh\n'
    'say sh.shout("hello")\n'
    'say sh.label\n', encoding='utf8')
r = _niko2('run', 'app.niko', cwd=e2e_proj)
assert r.returncode == 0, r.stderr or r.stdout
assert r.stdout == 'HELLO!\nshapes-lib\n', repr(r.stdout)
print('ok: e2e pkg import on VM (incl. package-internal relative import)')

node = shutil.which('node')
if node:
    r = _niko2('wasm', 'app.niko', '--run', cwd=e2e_proj)
    assert r.returncode == 0, r.stderr or r.stdout
    assert _strip_banner(r.stdout) == 'HELLO!\nshapes-lib\n', repr(r.stdout)
    print('ok: e2e pkg import on WASM')
else:
    print('SKIP e2e pkg import on WASM: node.js not on PATH')

# -- 6. lock-pin vs newest-fallback ------------------------------------------
vsrc = pathlib.Path(tempfile.mkdtemp(prefix='niko-pkg-ver-src-'))
_write_pkg(vsrc / 'v1', 'verlib', '1.0.0',
           {'main.niko': 'to tag -> text:\n    give back "v1"\n'})
_write_pkg(vsrc / 'v2', 'verlib', '2.0.0',
           {'main.niko': 'to tag -> text:\n    give back "v2"\n'})
install_package(str(vsrc / 'v1'))
app_src = 'import "pkg:verlib/main.niko" as v\nsay v.tag()\n'
projA = pathlib.Path(tempfile.mkdtemp(prefix='niko-pkg-lockA-'))
(projA / 'app.niko').write_text(app_src, encoding='utf8')
r = _niko2('lock', str(projA), cwd=projA)
assert r.returncode == 0, r.stderr or r.stdout
lockA = json.loads((projA / 'niko.lock').read_text(encoding='utf8'))
assert lockA['packages']['verlib']['version'] == '1.0.0', lockA
assert locked_package_versions(projA) == {'verlib': '1.0.0'}
# walk-up: a file nested deeper in the project sees the same pin
assert locked_package_versions(projA / 'sub' / 'deeper') == {'verlib': '1.0.0'}

install_package(str(vsrc / 'v2'))  # newest is now 2.0.0
assert [v for _, v, _ in installed_versions('verlib')] == ['1.0.0', '2.0.0']

# pinned project still resolves 1.0.0 (run and check both honor the lock)
r = _niko2('run', 'app.niko', cwd=projA)
assert r.returncode == 0, r.stderr or r.stdout
assert r.stdout == 'v1\n', repr(r.stdout)
r = _niko2('check', 'app.niko', cwd=projA)
assert r.returncode == 0, r.stderr or r.stdout
# re-locking never silently upgrades the pin
r = _niko2('lock', str(projA), cwd=projA)
assert r.returncode == 0, r.stderr or r.stdout
lockA2 = json.loads((projA / 'niko.lock').read_text(encoding='utf8'))
assert lockA2['packages']['verlib']['version'] == '1.0.0', lockA2
# in-process lock builder agrees, and keeps the existing pin too
lp = lock_packages_for_project(projA)
assert lp['verlib']['version'] == '1.0.0', lp

# no lockfile -> newest cached version wins
projB = pathlib.Path(tempfile.mkdtemp(prefix='niko-pkg-lockB-'))
(projB / 'app.niko').write_text(app_src, encoding='utf8')
assert not (projB / 'niko.lock').exists()
r = _niko2('run', 'app.niko', cwd=projB)
assert r.returncode == 0, r.stderr or r.stdout
assert r.stdout == 'v2\n', repr(r.stdout)
assert locked_package_versions(projB) == {}
# a lock pinning a version that is NOT installed is a clear error
(projA / 'niko.lock').write_text(json.dumps(
    {'version': 1, 'dependencies': {},
     'packages': {'verlib': {'version': '9.9.9', 'source': 'unknown'}}}),
    encoding='utf8')
r = _niko2('run', 'app.niko', cwd=projA)
assert r.returncode != 0, 'locked-but-missing version unexpectedly ran'
assert 'locked to version 9.9.9' in r.stdout, r.stdout
assert 'niko2 get' in r.stdout, r.stdout
print('ok: lock-pin vs newest-fallback')

# -- 7. deps / lock integration ------------------------------------------------
projC = pathlib.Path(tempfile.mkdtemp(prefix='niko-pkg-deps-'))
(projC / 'app.niko').write_text(
    'import "pkg:shapes/main.niko" as sh\nsay sh.label\n', encoding='utf8')
r = _niko2('deps', str(projC), cwd=projC)
assert r.returncode == 0, r.stderr or r.stdout
assert 'pkg:shapes/main.niko' in r.stdout, r.stdout
# in-process scanner sees the same spec (and skips unparseable files)
(projC / 'broken.niko').write_text('import "pkg:shapes/main.niko" as\n', encoding='utf8')
specs = collect_package_imports(projC)
assert specs == {'app.niko': ['pkg:shapes/main.niko']}, specs
r = _niko2('lock', str(projC), cwd=projC)
assert r.returncode == 0, r.stderr or r.stdout
lockC = json.loads((projC / 'niko.lock').read_text(encoding='utf8'))
shapes_pin = lockC['packages']['shapes']
assert shapes_pin['version'] == '1.0.0', lockC
assert shapes_pin['source'] == str((e2e_src / 'shapes').resolve()), lockC
# lock on a project importing a package that is not installed: clear error
projD = pathlib.Path(tempfile.mkdtemp(prefix='niko-pkg-deps-err-'))
(projD / 'app.niko').write_text(
    'import "pkg:ghostlib/main.niko" as g\nsay 1\n', encoding='utf8')
r = _niko2('lock', str(projD), cwd=projD)
assert r.returncode != 0, 'lock unexpectedly succeeded without the package'
assert 'not installed' in r.stdout, r.stdout
assert 'niko2 get' in r.stdout, r.stdout
print('ok: deps/lock integration')

# -- 8. error cases with line/col diagnostics ----------------------------------
perr = pathlib.Path(tempfile.mkdtemp(prefix='niko-pkg-err-'))


def _run_case(name, src, *needles):
    (perr / name).write_text(src, encoding='utf8')
    r = _niko2('run', name, cwd=perr)
    assert r.returncode != 0, f'error[{name}] unexpectedly succeeded'
    for n in needles:
        assert n in r.stdout, f'error[{name}] missing {n!r}:\n{r.stdout!r}'
    return r


# unknown package, reported on the import's line (line 2 here)
_run_case('unknown.niko',
          'say "one"\nimport "pkg:nope/main.niko" as n\n',
          'unknown package "nope"', "run 'niko2 get", 'line 2')
# missing file inside an installed package
_run_case('missingfile.niko',
          'import "pkg:shapes/nope.niko" as s\n',
          'cannot find module "pkg:shapes/nope.niko"', 'shapes-1.0.0', 'line 1')
# .. traversal out of the package dir is rejected
_run_case('traversal.niko',
          'import "pkg:shapes/../evil.niko" as s\n',
          'escapes the package directory', 'line 1')
# non-.niko path inside a package
_run_case('badsuffix.niko',
          'import "pkg:shapes/readme.txt" as s\n',
          'must name a .niko file')
# malformed spec: no file path
_run_case('nospec.niko',
          'import "pkg:shapes" as s\n',
          'needs a package name and a file path')
print('ok: error cases with line/col')

# -- 9. NIKO_PKG_CACHE override honored by install + resolution ----------------
envA = pathlib.Path(tempfile.mkdtemp(prefix='niko-pkg-envA-'))
envB = pathlib.Path(tempfile.mkdtemp(prefix='niko-pkg-envB-'))
assert default_cache_root() == SESSION / 'cache'  # env var is being read
env_src = pathlib.Path(tempfile.mkdtemp(prefix='niko-pkg-env-src-'))
_write_pkg(env_src / 'envpkg', 'envpkg', '1.0.0',
           {'main.niko': 'to who -> text:\n    give back "env-a"\n'})
r = _niko2('get', str(env_src / 'envpkg'), cwd=proj,
           env={'NIKO_PKG_CACHE': str(envA)})
assert r.returncode == 0, r.stderr or r.stdout
assert (envA / 'envpkg-1.0.0' / 'main.niko').is_file()
assert not (envB / 'envpkg-1.0.0').exists()
assert not (SESSION / 'cache' / 'envpkg-1.0.0').exists()
env_proj = pathlib.Path(tempfile.mkdtemp(prefix='niko-pkg-env-proj-'))
(env_proj / 'app.niko').write_text(
    'import "pkg:envpkg/main.niko" as e\nsay e.who()\n', encoding='utf8')
# resolution reads the same override: found under envA ...
r = _niko2('run', 'app.niko', cwd=env_proj, env={'NIKO_PKG_CACHE': str(envA)})
assert r.returncode == 0, r.stderr or r.stdout
assert r.stdout == 'env-a\n', repr(r.stdout)
# ... and unknown under an empty override dir
r = _niko2('run', 'app.niko', cwd=env_proj, env={'NIKO_PKG_CACHE': str(envB)})
assert r.returncode != 0
assert 'unknown package "envpkg"' in r.stdout, r.stdout
print('ok: NIKO_PKG_CACHE override')

# -- 10. stdlib coexistence ----------------------------------------------------
std_proj = pathlib.Path(tempfile.mkdtemp(prefix='niko-pkg-std-'))
# a package setup is present: a pkg import + a lockfile pinning it
(std_proj / 'app.niko').write_text(
    'import "stdlib/text.niko" as text\n'
    'import "pkg:shapes/main.niko" as sh\n'
    'say text.reverse_text("abc")\n'
    'say sh.label\n', encoding='utf8')
r = _niko2('lock', str(std_proj), cwd=std_proj)
assert r.returncode == 0, r.stderr or r.stdout
lockS = json.loads((std_proj / 'niko.lock').read_text(encoding='utf8'))
assert 'shapes' in lockS['packages'], lockS  # package setup really present
r = _niko2('run', 'app.niko', cwd=std_proj)
assert r.returncode == 0, r.stderr or r.stdout
assert r.stdout == 'cba\nshapes-lib\n', repr(r.stdout)
print('ok: stdlib coexistence')

print('test_packages.py: all assertions passed')
