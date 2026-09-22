#!/usr/bin/env python3
"""Alpha 19: package registry + version-range solving tests.

Covers `niko2/semver.py` (range grammar + solver), `niko2/registry.py`
(index protocol, publish, install, update, registry selection,
disambiguation) and the CLI surface (`niko2 get <name>[@<range>]`,
`niko2 get --update <name>`, `niko2 publish`, `--registry`), plus the
`[dependencies]` manifest table in `niko2/packages.py`.

Hermetic by construction: every registry is a local directory fixture,
`NIKO_PKG_CACHE` points at a throwaway dir for the whole run, and
`NIKO_REGISTRY` points at the fixture (or is cleared for the selection
tests, where `HOME` is pointed at a throwaway dir so `~/.niko/config.toml`
is never the real one). No real network is ever used; the real `~/.niko`
is never touched (asserted at the end).

Run:  python3 tests/test_registry.py
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile
import tempfile

root = pathlib.Path(__file__).resolve().parent
project_root = root.parent
sys.path.insert(0, str(project_root))

# One throwaway sandbox for the whole script. Every subprocess below
# inherits os.environ, so installs + resolution can never reach the real
# ~/.niko. NIKO_REGISTRY points at the fixture registry (local dir, no
# network); individual tests override/clear it as needed.
SESSION = pathlib.Path(tempfile.mkdtemp(prefix='niko-regtest-'))
REG = SESSION / 'registry'
REG.mkdir(parents=True)
os.environ['NIKO_PKG_CACHE'] = str(SESSION / 'cache')
os.environ['NIKO_REGISTRY'] = str(REG)

from niko2 import registry as regmod  # noqa: E402
from niko2 import semver  # noqa: E402
from niko2.packages import (  # noqa: E402
    PackageError,
    parse_manifest,
    install_package,
)
from niko2.registry import (  # noqa: E402
    is_registry_spec,
    split_registry_spec,
    publish_package,
    install_from_registry,
    resolve_registry,
    update_package,
)

# The real ~/.niko must stay untouched: record its initial state so the
# hermeticity check at the end can compare.
_real_niko = pathlib.Path.home() / '.niko'
_real_niko_had_config = (_real_niko / 'config.toml').exists()
_real_niko_existed = _real_niko.exists()


def _niko2(*args, cwd, env=None):
    e = dict(os.environ)
    e['PYTHONPATH'] = str(project_root)
    if env:
        e.update(env)
    return subprocess.run([sys.executable, '-m', 'niko2', *args],
                          capture_output=True, text=True, cwd=cwd, env=e)


def _expect_cli_error(args, cwd, *needles, env=None):
    """CLI run must fail with every needle in stdout."""
    r = _niko2(*args, cwd=cwd, env=env)
    assert r.returncode != 0, f'{" ".join(args)} unexpectedly succeeded'
    for n in needles:
        assert n in r.stdout, \
            f'{" ".join(args)} missing {n!r}:\n{r.stdout!r}\n{r.stderr!r}'
    return r


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


def _write_pkg(src_dir, name, version, files, entry='main.niko',
               deps=None, description=''):
    """Write a package fixture dir: niko.toml + .niko files. Returns dir."""
    src_dir = pathlib.Path(src_dir)
    src_dir.mkdir(parents=True, exist_ok=True)
    manifest = (f'[package]\nname = "{name}"\nversion = "{version}"\n'
                f'entry = "{entry}"\n')
    if description:
        manifest += f'description = "{description}"\n'
    if deps:
        manifest += '[dependencies]\n'
        for k, v in deps.items():
            manifest += f'{k} = "{v}"\n'
    (src_dir / 'niko.toml').write_text(manifest, encoding='utf8')
    for rel, content in files.items():
        p = src_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding='utf8')
    return src_dir


def _publish_cli(pkg_dir, *extra):
    r = _niko2('publish', '--registry', str(REG), *extra, cwd=pkg_dir)
    assert r.returncode == 0, r.stderr or r.stdout
    return r


def _cache(*parts):
    return SESSION / 'cache' / pathlib.Path(*parts)


# -- 1. semver: range grammar + solver ----------------------------------------
def _sat(v, rng):
    return semver.satisfies(v, rng)


# caret: leftmost non-zero specified component pinned
assert _sat('1.2.3', '^1.2.3') and _sat('1.9.9', '^1.2.3')
assert not _sat('2.0.0', '^1.2.3') and not _sat('1.2.2', '^1.2.3')
assert _sat('0.2.9', '^0.2.3') and not _sat('0.3.0', '^0.2.3')
assert _sat('0.0.3', '^0.0.3') and not _sat('0.0.4', '^0.0.3')
assert _sat('0.5.0', '^0') and not _sat('1.0.0', '^0')
assert _sat('1.2.5', '^1.2') and not _sat('2.0.0', '^1.2')
# tilde: same minor (same major for ~1)
assert _sat('1.2.5', '~1.2.3') and not _sat('1.3.0', '~1.2.3')
assert _sat('1.2.9', '~1.2') and not _sat('1.3.0', '~1.2')
assert _sat('1.9.9', '~1') and not _sat('2.0.0', '~1')
# bare partials: 1.2 = 1.2.x, 1 = 1.x
assert _sat('1.2.0', '1.2') and _sat('1.2.9', '1.2') and not _sat('1.3.0', '1.2')
assert _sat('1.0.0', '1') and _sat('1.9.9', '1') and not _sat('2.0.0', '1')
assert _sat('1.2.3', '1.2.3') and not _sat('1.2.4', '1.2.3')
# comparators + comma AND
assert _sat('1.5.0', '>=1.0, <2.0') and not _sat('2.0.0', '>=1.0, <2.0')
assert not _sat('0.9.9', '>=1.0, <2.0')
assert _sat('2.0.0', '>1.0') and not _sat('1.0.0', '>1.0')
assert _sat('2.0.0', '<=2.0') and not _sat('2.0.1', '<=2.0')
assert _sat('1.2.3', '=1.2.3') and _sat('1.2.3', '==1.2.3')
assert _sat('1.2.0', '=1.2') and _sat('1.2.9', '=1.2')  # = on partial = bare
assert _sat('1.2.3', '*') and _sat('0.0.1', '*')
assert _sat('9.9.9', '')  # empty range matches everything
assert _sat('1.5.0', '>=1.0')  # comparator with partial operand pads
assert not _sat('0.9.9', '>=1')
assert _sat('1.0.0', '>=1') and _sat('1.2.3', '<2')
# max_satisfying picks the highest match
vs = ['1.0.0', '1.1.0', '2.0.0']
assert semver.max_satisfying(vs, '^1.0') == '1.1.0'
assert semver.max_satisfying(vs, '*') == '2.0.0'
assert semver.max_satisfying(vs, '2') == '2.0.0'
assert semver.max_satisfying(vs, '^9.0') is None
assert semver.max_satisfying(['garbage', '1.0.0'], '*') == '1.0.0'  # skips junk
assert semver.max_satisfying([], '*') is None
# failures are plain-English SemverErrors
for bad in ['^', '~', 'foo', '>=', '>=1.0,,<2.0', '1.2.3.4', '^1.2.x']:
    try:
        semver.parse_range(bad)
    except semver.SemverError as e:
        assert str(e), f'empty message for {bad!r}'
    else:
        raise AssertionError(f'parse_range({bad!r}) should have failed')
try:
    semver.parse_version('1.2')
except semver.SemverError as e:
    assert 'strict X.Y.Z' in str(e)
else:
    raise AssertionError('parse_version("1.2") should have failed')
try:
    semver.satisfies('1.0.0', 'nope')
except semver.SemverError:
    pass
else:
    raise AssertionError('satisfies with bad range should have failed')
print('ok: semver range grammar + solver')

# -- 2. disambiguation: registry spec vs dir/git ------------------------------
assert is_registry_spec('greet') is True
assert is_registry_spec('greet@^1.0') is True
assert is_registry_spec('greet@1.0.0') is True
assert is_registry_spec('greet@>=1.0, <2.0') is True
assert is_registry_spec('./greet') is False      # dir path
assert is_registry_spec('greet.git') is False    # git URL
assert is_registry_spec('https://x/y.git') is False
assert is_registry_spec('git@h:x/y.git') is False
assert is_registry_spec('file:///x/y') is False
assert is_registry_spec('') is False
assert split_registry_spec('greet') == ('greet', '*')
assert split_registry_spec('greet@^1.0') == ('greet', '^1.0')
assert split_registry_spec('  greet @ 1.2.3 ') == ('greet', '1.2.3')
_expect_pkg_error(lambda: split_registry_spec('greet@'), 'empty version range')
_expect_pkg_error(lambda: split_registry_spec('greet@foo'), 'niko2 get')
print('ok: is_registry_spec / split_registry_spec')

# -- 3. manifest [dependencies] validation ------------------------------------
tdep = pathlib.Path(tempfile.mkdtemp(prefix='niko-reg-deps-'))
p = tdep / 'good.toml'
p.write_text('[package]\nname = "app"\nversion = "1.0.0"\n'
             '[dependencies]\nlib = "^1.0"\nbase = ">=2.0, <3.0"\n',
             encoding='utf8')
m = parse_manifest(p)
assert m.dependencies == {'lib': '^1.0', 'base': '>=2.0, <3.0'}, m.dependencies
p = tdep / 'nodeps.toml'
p.write_text('[package]\nname = "app"\nversion = "1.0.0"\n', encoding='utf8')
assert parse_manifest(p).dependencies == {}
p = tdep / 'baddepname.toml'
p.write_text('[package]\nname = "app"\nversion = "1.0.0"\n'
             '[dependencies]\n"1bad" = "^1.0"\n', encoding='utf8')
_expect_pkg_error(lambda: parse_manifest(p), 'bad dependency name', '"1bad"')
p = tdep / 'badeprange.toml'
p.write_text('[package]\nname = "app"\nversion = "1.0.0"\n'
             '[dependencies]\nlib = "whenever"\n', encoding='utf8')
_expect_pkg_error(lambda: parse_manifest(p), 'dependency "lib"')
p = tdep / 'notatable.toml'
p.write_text('dependencies = 42\n[package]\nname = "app"\nversion = "1.0.0"\n',
             encoding='utf8')
_expect_pkg_error(lambda: parse_manifest(p), '"[dependencies]" must be a table')
print('ok: manifest [dependencies] validation')

# -- 4. publish: index + tarball layout ----------------------------------------
psrc = pathlib.Path(tempfile.mkdtemp(prefix='niko-reg-pub-src-'))
greet_src = _write_pkg(psrc / 'greetpkg', 'greetpkg', '1.0.0',
                       {'main.niko': 'to hello with who:\n'
                                     '    give back "hi " + who\n'},
                       description='A greeting library.')
# publish refuses when the entry file is missing
_write_pkg(psrc / 'noentry', 'noentry', '1.0.0', {'other.niko': 'say 1\n'})
_expect_cli_error(('publish', '--registry', str(REG)), psrc / 'noentry',
                  'entry "main.niko" not found')

# three versions of greetpkg into the fixture registry
for v in ('1.0.0', '1.1.0', '2.0.0'):
    d = psrc / f'greetpkg-{v}'
    _write_pkg(d, 'greetpkg', v,
               {'main.niko': f'to hello with who:\n    give back "hi " + who\n'
                             f'set ver to "{v}"\n'},
               description='A greeting library.')
    r = _publish_cli(d)
    assert f'✓ published greetpkg {v} to {REG}' in r.stdout, r.stdout

index_path = REG / 'index.json'
assert index_path.is_file()
index = json.loads(index_path.read_text(encoding='utf8'))
pkgs = index['packages']['greetpkg']
assert set(pkgs) == {'1.0.0', '1.1.0', '2.0.0'}, pkgs
e = pkgs['2.0.0']
assert e['url'] == 'tarballs/greetpkg-2.0.0.tar.gz', e
assert len(e['sha256']) == 64 and all(c in '0123456789abcdef' for c in e['sha256']), e
assert e['description'] == 'A greeting library.', e
assert e['dependencies'] == {}, e
assert (REG / 'tarballs' / 'greetpkg-2.0.0.tar.gz').is_file()
# tarball contents: niko.toml + sources, no .git/__pycache__/*.pyc
junk_src = psrc / 'junkpkg'
_write_pkg(junk_src, 'junkpkg', '0.1.0', {'main.niko': 'say 1\n'})
(junk_src / '.git').mkdir()
(junk_src / '.git' / 'HEAD').write_text('ref\n', encoding='utf8')
(junk_src / '__pycache__').mkdir()
(junk_src / '__pycache__' / 'x.pyc').write_bytes(b'\x00')
(junk_src / 'junk.pyc').write_bytes(b'\x00')
_publish_cli(junk_src)
with tarfile.open(REG / 'tarballs' / 'junkpkg-0.1.0.tar.gz', 'r:gz') as tf:
    names = tf.getnames()
assert 'niko.toml' in names and 'main.niko' in names, names
assert not any('.git' in n or '__pycache__' in n or n.endswith('.pyc')
               for n in names), names
print('ok: publish writes index + tarballs (with excludes)')

# publish refuses to overwrite without --force; --force re-publishes
r = _niko2('publish', '--registry', str(REG), cwd=psrc / 'greetpkg-2.0.0')
assert r.returncode != 0
assert 'already published' in r.stdout and '--force' in r.stdout, r.stdout
r = _niko2('publish', '--registry', str(REG), '--force',
           cwd=psrc / 'greetpkg-2.0.0')
assert r.returncode == 0, r.stderr or r.stdout
assert '✓ published greetpkg 2.0.0' in r.stdout, r.stdout
# publish with no registry configured: clear error
home_empty = SESSION / 'home-empty'
(home_empty / '.niko').mkdir(parents=True)
_expect_cli_error(('publish',), psrc / 'greetpkg-2.0.0',
                  'no registry configured', 'NIKO_REGISTRY',
                  env={'NIKO_REGISTRY': '', 'HOME': str(home_empty)})
# publish to a remote registry: the auth error, via flag and via env
for env in ({'NIKO_REGISTRY': 'https://example.com/niko/index.json'},
            {}):
    args = ['publish']
    if not env:
        args += ['--registry', 'https://example.com/niko/index.json']
    r = _niko2(*args, cwd=psrc / 'greetpkg-2.0.0', env=env or None)
    assert r.returncode != 0, args
    assert 'publishing needs auth' in r.stdout and 'not supported yet' in r.stdout, \
        r.stdout
    assert 'local directory registry' in r.stdout, r.stdout
print('ok: publish guards (overwrite, missing registry, remote auth)')

# -- 5. get by name: newest version + lockfile pin -----------------------------
proj = pathlib.Path(tempfile.mkdtemp(prefix='niko-reg-proj-'))
r = _niko2('get', 'greetpkg', cwd=proj)
assert r.returncode == 0, r.stderr or r.stdout
assert '✓ installed greetpkg 2.0.0' in r.stdout, r.stdout
gdir = _cache('greetpkg-2.0.0')
assert gdir.is_dir() and (gdir / 'main.niko').is_file()
src = json.loads((gdir / '.niko-source.json').read_text(encoding='utf8'))
assert src['kind'] == 'registry' and src['range'] == '*', src
assert src['source'] == f'registry:{REG}', src
lock = json.loads((proj / 'niko.lock').read_text(encoding='utf8'))
pin = lock['packages']['greetpkg']
assert pin == {'version': '2.0.0', 'source': f'registry:{REG}', 'range': '*'}, pin
# the installed package imports and runs end to end
(proj / 'app.niko').write_text(
    'import "pkg:greetpkg/main.niko" as g\nsay g.hello("Casper")\nsay g.ver\n',
    encoding='utf8')
r = _niko2('run', 'app.niko', cwd=proj)
assert r.returncode == 0, r.stderr or r.stdout
assert r.stdout == 'hi Casper\n2.0.0\n', repr(r.stdout)
print('ok: get <name> installs newest + writes pin')

# -- 6. get by range: max satisfying, exact ------------------------------------
proj2 = pathlib.Path(tempfile.mkdtemp(prefix='niko-reg-proj2-'))
r = _niko2('get', 'greetpkg@^1.0', cwd=proj2)
assert r.returncode == 0, r.stderr or r.stdout
assert '✓ installed greetpkg 1.1.0' in r.stdout, r.stdout
lock2 = json.loads((proj2 / 'niko.lock').read_text(encoding='utf8'))
assert lock2['packages']['greetpkg']['version'] == '1.1.0', lock2
assert lock2['packages']['greetpkg']['range'] == '^1.0', lock2
proj3 = pathlib.Path(tempfile.mkdtemp(prefix='niko-reg-proj3-'))
r = _niko2('get', 'greetpkg@1.0.0', cwd=proj3)
assert r.returncode == 0, r.stderr or r.stdout
assert '✓ installed greetpkg 1.0.0' in r.stdout, r.stdout
assert _cache('greetpkg-1.0.0').is_dir()
# empty range after @: clear error (not a registry-vs-dir misfire)
_expect_cli_error(('get', 'greetpkg@'), proj3, 'empty version range')
# unresolvable range: names the range and what the registry has
_expect_cli_error(('get', 'greetpkg@^9.0'), proj3,
                  'no version of "greetpkg" satisfies "^9.0"',
                  '1.0.0', '1.1.0', '2.0.0')
# unknown package: clear error naming the registry
_expect_cli_error(('get', 'nosuchpkg'), proj3,
                  'package "nosuchpkg" not found in registry', str(REG))
print('ok: get <name>@<range> (max satisfying, exact, errors)')

# -- 7. lock keeps the pin; plain get stays idempotent --------------------------
# re-locking keeps the pinned version (never silently upgrades)
r = _niko2('lock', str(proj), cwd=proj)
assert r.returncode == 0, r.stderr or r.stdout
lock_again = json.loads((proj / 'niko.lock').read_text(encoding='utf8'))
assert lock_again['packages']['greetpkg']['version'] == '2.0.0', lock_again
assert lock_again['packages']['greetpkg']['range'] == '*', lock_again
# fresh get of the same name+range: idempotent, no reinstall
r = _niko2('get', 'greetpkg', cwd=proj)
assert r.returncode == 0, r.stderr or r.stdout
assert 'already installed' in r.stdout and '--force' in r.stdout, r.stdout
# in-process lock builder keeps the range too
from niko2.packages import lock_packages_for_project  # noqa: E402
lp = lock_packages_for_project(proj)
assert lp['greetpkg'] == {'version': '2.0.0',
                          'source': f'registry:{REG}', 'range': '*'}, lp
print('ok: lock keeps pin; get stays idempotent')

# -- 8. get --update: re-resolve, upgrade, no-op ---------------------------------
# publish a newer version, then update (both flag positions work)
up_src = psrc / 'greetpkg-2.1.0'
_write_pkg(up_src, 'greetpkg', '2.1.0',
           {'main.niko': 'to hello with who:\n    give back "hi " + who\n'
                         'set ver to "2.1.0"\n'},
           description='A greeting library.')
_publish_cli(up_src)
r = _niko2('get', '--update', 'greetpkg', cwd=proj)
assert r.returncode == 0, r.stderr or r.stdout
assert '✓ updated greetpkg 2.0.0 → 2.1.0' in r.stdout, r.stdout
assert _cache('greetpkg-2.1.0').is_dir()
lock_up = json.loads((proj / 'niko.lock').read_text(encoding='utf8'))
assert lock_up['packages']['greetpkg']['version'] == '2.1.0', lock_up
assert lock_up['packages']['greetpkg']['range'] == '*', lock_up
# running the app now uses the new version
r = _niko2('run', 'app.niko', cwd=proj)
assert r.returncode == 0, r.stderr or r.stdout
assert r.stdout == 'hi Casper\n2.1.0\n', repr(r.stdout)
# already newest: friendly no-op (trailing flag position)
r = _niko2('get', 'greetpkg', '--update', cwd=proj)
assert r.returncode == 0, r.stderr or r.stdout
assert 'already at the newest matching version (2.1.0)' in r.stdout, r.stdout
# update honors the lockfile range: publish 1.2.0, but ^1.0 stays in 1.x
_write_pkg(psrc / 'greetpkg-1.2.0', 'greetpkg', '1.2.0',
           {'main.niko': 'set ver to "1.2.0"\n'})
_publish_cli(psrc / 'greetpkg-1.2.0')
r = _niko2('get', '--update', 'greetpkg', cwd=proj2)  # pinned range ^1.0
assert r.returncode == 0, r.stderr or r.stdout
assert '✓ updated greetpkg 1.1.0 → 1.2.0' in r.stdout, r.stdout
lock2u = json.loads((proj2 / 'niko.lock').read_text(encoding='utf8'))
assert lock2u['packages']['greetpkg'] == {
    'version': '1.2.0', 'source': f'registry:{REG}', 'range': '^1.0'}, lock2u
# update error cases
proj_no_lock = pathlib.Path(tempfile.mkdtemp(prefix='niko-reg-nolock-'))
_expect_cli_error(('get', '--update', 'greetpkg'), proj_no_lock,
                  'no niko.lock found', 'niko2 get greetpkg@<range>')
_expect_cli_error(('get', '--update', 'greetpkg@^1.0'), proj,
                  'takes a plain package name', 'niko2 get greetpkg@<range>')
# a non-registry pin cannot be updated
local_src = _write_pkg(psrc / 'localonly', 'localonly', '1.0.0',
                       {'main.niko': 'say 1\n'})
r = _niko2('get', str(local_src), cwd=proj_no_lock)
assert r.returncode == 0, r.stderr or r.stdout
(proj_no_lock / 'app.niko').write_text(
    'import "pkg:localonly/main.niko" as l\nsay 1\n', encoding='utf8')
r = _niko2('lock', str(proj_no_lock), cwd=proj_no_lock)
assert r.returncode == 0, r.stderr or r.stdout
_expect_cli_error(('get', '--update', 'localonly'), proj_no_lock,
                  'only upgrades registry packages', str(local_src))
# in-process update_package agrees
upd = update_package('greetpkg', cwd=str(proj))
assert (upd.name, upd.old_version, upd.new_version, upd.changed) == \
    ('greetpkg', '2.1.0', '2.1.0', False), upd
print('ok: get --update (upgrade, range, no-op, errors)')

# -- 9. checksum mismatch: hard error, bad download gone -----------------------
_write_pkg(psrc / 'badpkg', 'badpkg', '1.0.0', {'main.niko': 'say 1\n'})
_publish_cli(psrc / 'badpkg')
tgz = REG / 'tarballs' / 'badpkg-1.0.0.tar.gz'
with open(tgz, 'r+b') as f:
    f.seek(-8, 2)
    f.write(b'CORRUPT!')
proj_bad = pathlib.Path(tempfile.mkdtemp(prefix='niko-reg-bad-'))
_expect_cli_error(('get', 'badpkg'), proj_bad, 'sha256 mismatch', 'badpkg',
                  '1.0.0', 'deleted the bad download')
assert not _cache('badpkg-1.0.0').exists(), 'corrupt package must not install'
print('ok: sha256 mismatch is a hard error')

# -- 10. registry selection -----------------------------------------------------
# file:// URL form
r = _niko2('get', 'greetpkg@1.0.0', '--force', cwd=proj_bad,
           env={'NIKO_REGISTRY': REG.as_uri()})
assert r.returncode == 0, r.stderr or r.stdout
assert '✓ installed greetpkg 1.0.0' in r.stdout, r.stdout
lock_f = json.loads((proj_bad / 'niko.lock').read_text(encoding='utf8'))
assert lock_f['packages']['greetpkg']['source'].startswith('registry:file://'), lock_f
# config file form: HOME pointed at a throwaway dir, env cleared
home_cfg = SESSION / 'home-cfg'
(home_cfg / '.niko').mkdir(parents=True)
(home_cfg / '.niko' / 'config.toml').write_text(
    '[registry]\nurl = "%s"\n' % REG, encoding='utf8')
proj_cfg = pathlib.Path(tempfile.mkdtemp(prefix='niko-reg-cfg-'))
r = _niko2('get', 'greetpkg@1.1.0', cwd=proj_cfg,
           env={'NIKO_REGISTRY': '', 'HOME': str(home_cfg)})
assert r.returncode == 0, r.stderr or r.stdout
assert 'greetpkg 1.1.0' in r.stdout, r.stdout  # installed or already cached
lock_cfg = json.loads((proj_cfg / 'niko.lock').read_text(encoding='utf8'))
pin_cfg = lock_cfg['packages']['greetpkg']
assert pin_cfg['version'] == '1.1.0', pin_cfg
assert pin_cfg['source'] == f'registry:{REG}', pin_cfg
assert pin_cfg['range'] == '1.1.0', pin_cfg
# env wins over the config file: a second registry holds only "onlyb"
regB = SESSION / 'registryB'
regB.mkdir()
_write_pkg(psrc / 'onlyb', 'onlyb', '1.0.0', {'main.niko': 'say "b"\n'})
r = _niko2('publish', '--registry', str(regB), cwd=psrc / 'onlyb')
assert r.returncode == 0, r.stderr or r.stdout
proj_env = pathlib.Path(tempfile.mkdtemp(prefix='niko-reg-env-'))
r = _niko2('get', 'onlyb', cwd=proj_env,
           env={'NIKO_REGISTRY': str(regB), 'HOME': str(home_cfg)})
assert r.returncode == 0, r.stderr or r.stdout
assert '✓ installed onlyb 1.0.0' in r.stdout, r.stdout
r = _niko2('get', 'onlyb', cwd=proj_env,
           env={'NIKO_REGISTRY': '', 'HOME': str(home_cfg)})
assert r.returncode != 0  # config's registry (REG) has no onlyb
assert 'not found in registry' in r.stdout, r.stdout
# bad scheme
_expect_cli_error(('get', 'greetpkg'), proj_env, 'bad registry',
                  env={'NIKO_REGISTRY': 'ftp://example.com/index.json'})
# no registry at all
_expect_cli_error(('get', 'greetpkg'), proj_env, 'no registry configured',
                  env={'NIKO_REGISTRY': '', 'HOME': str(home_empty)})
# registry dir without an index
reg_empty = SESSION / 'registry-empty'
reg_empty.mkdir()
_expect_cli_error(('get', 'greetpkg'), proj_env, 'has no index.json',
                  'not a Niko registry',
                  env={'NIKO_REGISTRY': str(reg_empty)})
# in-process resolve_registry agrees on the three forms
assert resolve_registry(str(REG)).kind == 'dir'
assert resolve_registry(REG.as_uri()).kind == 'dir'
assert resolve_registry('https://example.com/niko/index.json').kind == 'remote'
print('ok: registry selection (dir, file://, config, env-wins, errors)')

# -- 11. disambiguation on the CLI ----------------------------------------------
# a local directory named exactly like a package: bare name = registry lookup
disco = pathlib.Path(tempfile.mkdtemp(prefix='niko-reg-disco-'))
_write_pkg(disco / 'dironly', 'dironly', '0.0.1', {'main.niko': 'say "dir"\n'})
_expect_cli_error(('get', 'dironly'), disco,
                  'package "dironly" not found in registry')
# ./ prefix forces directory handling
r = _niko2('get', './dironly', cwd=disco)
assert r.returncode == 0, r.stderr or r.stdout
assert '✓ installed dironly 0.0.1' in r.stdout, r.stdout
assert _cache('dironly-0.0.1').is_dir()
# an unparseable range is not a registry spec either: clean dir/git error
_expect_cli_error(('get', 'dironly@junk-range'), disco,
                  'is not a directory and not a git URL')
# file:// git URL still goes through git handling (no registry misfire)
git = shutil.which('git')
if git is None:
    print('SKIP file:// git get: git not on PATH')
else:
    grepo = pathlib.Path(tempfile.mkdtemp(prefix='niko-reg-gitrepo-'))
    _write_pkg(grepo, 'gitlib2', '0.4.0',
               {'main.niko': 'to triple with x:\n    give back x * 3\n'})
    subprocess.run([git, 'init', '-q'], cwd=grepo, check=True)
    subprocess.run([git, 'add', '-A'], cwd=grepo, check=True)
    subprocess.run([git, '-c', 'user.email=t@t', '-c', 'user.name=t',
                    'commit', '-qm', 'init'], cwd=grepo, check=True)
    r = _niko2('get', grepo.as_uri(), cwd=disco)
    assert r.returncode == 0, r.stderr or r.stdout
    assert '✓ installed gitlib2 0.4.0' in r.stdout, r.stdout
    assert not (_cache('gitlib2-0.4.0') / '.git').exists()
    print('ok: file:// git get unaffected')
print('ok: disambiguation (bare name -> registry, ./ -> directory)')

# -- 12. transitive [dependencies] -----------------------------------------------
# chain: app -> lib ^1.0 -> base >=1.0; registry has lib 1.0.0/1.1.0, base 1.0.0
_write_pkg(psrc / 'base', 'base', '1.0.0',
           {'main.niko': 'set base_ver to "1.0.0"\n'})
_publish_cli(psrc / 'base')
for v in ('1.0.0', '1.1.0'):
    d = psrc / f'lib-{v}'
    _write_pkg(d, 'lib', v,
               {'main.niko': f'set lib_ver to "{v}"\n'},
               deps={'base': '>=1.0'})
    _publish_cli(d)
_write_pkg(psrc / 'app', 'app', '1.0.0',
           {'main.niko': 'import "pkg:lib/main.niko" as l\n'
                         'import "pkg:base/main.niko" as b\n'
                         'say l.lib_ver + " / " + b.base_ver\n'},
           deps={'lib': '^1.0'})
_publish_cli(psrc / 'app')
# the published index records the dependency ranges
app_entry = json.loads((REG / 'index.json').read_text(encoding='utf8'))
assert app_entry['packages']['app']['1.0.0']['dependencies'] == \
    {'lib': '^1.0'}, app_entry['packages']['app']['1.0.0']
proj_app = pathlib.Path(tempfile.mkdtemp(prefix='niko-reg-app-'))
r = _niko2('get', 'app', cwd=proj_app)
assert r.returncode == 0, r.stderr or r.stdout
assert '✓ installed app 1.0.0' in r.stdout, r.stdout
# full transitive closure installed from the same registry, max satisfying
assert _cache('app-1.0.0').is_dir()
assert _cache('lib-1.1.0').is_dir(), 'dep lib ^1.0 should resolve to 1.1.0'
assert _cache('base-1.0.0').is_dir()
print('ok: transitive [dependencies] install (max satisfying)')

# already-satisfied dep is left alone: pre-install lib 1.0.0 exactly, then
# get app2 (deps lib ^1.0) -> lib stays at 1.0.0, no 1.1.0 appears... except
# 1.1.0 is already cached from above, so use a fresh cache for this check.
fresh_cache = SESSION / 'cache2'
fresh_cache.mkdir()
env_fresh = {'NIKO_PKG_CACHE': str(fresh_cache)}
r = _niko2('get', 'lib@1.0.0', cwd=proj_app, env=env_fresh)
assert r.returncode == 0, r.stderr or r.stdout
r = _niko2('get', 'app', cwd=proj_app, env=env_fresh)
assert r.returncode == 0, r.stderr or r.stdout
assert (fresh_cache / 'lib-1.0.0').is_dir()
assert not (fresh_cache / 'lib-1.1.0').exists(), \
    'satisfied dep must not be re-resolved'
assert (fresh_cache / 'base-1.0.0').is_dir(), 'transitive dep still installed'
print('ok: satisfied dependencies are not reinstalled')

# dependency cycle a<->b terminates (module import cycles are still an
# error at import time; this is about the installer not looping)
_write_pkg(psrc / 'cyca', 'cyca', '1.0.0', {'main.niko': 'say "a"\n'},
           deps={'cycb': '^1.0'})
_write_pkg(psrc / 'cycb', 'cycb', '1.0.0', {'main.niko': 'say "b"\n'},
           deps={'cyca': '^1.0'})
_publish_cli(psrc / 'cyca')
_publish_cli(psrc / 'cycb')
r = _niko2('get', 'cyca', cwd=proj_app, env=env_fresh)
assert r.returncode == 0, r.stderr or r.stdout
assert (fresh_cache / 'cyca-1.0.0').is_dir()
assert (fresh_cache / 'cycb-1.0.0').is_dir()
print('ok: dependency cycle terminates')

# unsatisfiable dependency: clear error naming the dep and the range
_write_pkg(psrc / 'needy', 'needy', '1.0.0', {'main.niko': 'say "n"\n'},
           deps={'lib': '^9.0'})
_publish_cli(psrc / 'needy')
_expect_cli_error(('get', 'needy'), proj_app,
                  'no version of "lib" satisfies "^9.0"', env=env_fresh)
print('ok: unsatisfiable dependency errors clearly')

# -- 13. in-process install_from_registry ---------------------------------------
res = install_from_registry('greetpkg', '^1.0')
assert res.manifest.version == '1.2.0' and res.fresh is False, res
_expect_pkg_error(lambda: install_from_registry('greetpkg', '^9.0'),
                  'no version of "greetpkg" satisfies "^9.0"')
_expect_pkg_error(lambda: install_from_registry('ghostpkg'),
                  'package "ghostpkg" not found in registry')
# a bad range surfaces as SemverError (plain English); the CLI converts it
# to PackageError in split_registry_spec, and is_registry_spec treats an
# unparseable range as "not a registry spec" (falls through to dir/git).
try:
    install_from_registry('greetpkg', 'junk-range')
except semver.SemverError as e:
    assert 'bad version range' in str(e), e
else:
    raise AssertionError('expected SemverError for junk-range')
# "no registry configured": env cleared AND no config file present
_saved_env = os.environ.pop('NIKO_REGISTRY', None)
_saved_cfg = regmod.CONFIG_FILE
regmod.CONFIG_FILE = SESSION / 'no-such-config.toml'
try:
    _expect_pkg_error(lambda: resolve_registry(''), 'no registry configured')
finally:
    if _saved_env is not None:
        os.environ['NIKO_REGISTRY'] = _saved_env
    regmod.CONFIG_FILE = _saved_cfg
print('ok: in-process install_from_registry + resolve_registry')

# -- 14. hermeticity: the real ~/.niko was never touched ------------------------
assert (_real_niko / 'config.toml').exists() == _real_niko_had_config
assert _real_niko.exists() == _real_niko_existed
print('ok: real ~/.niko untouched')

print('test_registry.py: all assertions passed')
