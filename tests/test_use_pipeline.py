#!/usr/bin/env python3
"""Alpha 22: `use` routed through the module pipeline.

Before Alpha 22, `use "file.niko"` was executed outside the module
pipeline (ModuleLoader/VMLoader), worked only on the VM (WASM/native
raised CompileError), silently ignored use cycles, and had no debugger
or LSP support. Now `use` goes through the same graph / check /
desugar pipeline as `import`, so it behaves byte-identically on all
three backends.

Hermetic: every case writes its own throwaway directory; no network.

Run:  python3 tests/test_use_pipeline.py
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

root = pathlib.Path(__file__).resolve().parent
project_root = root.parent
sys.path.insert(0, str(project_root))

PASS = 0


def _ok(name):
    global PASS
    PASS += 1
    print(f'  ok {name}')


def _niko2(*args, cwd=None, input_text=None):
    env = dict(os.environ, PYTHONPATH=str(project_root))
    return subprocess.run(
        [sys.executable, '-m', 'niko2', *args],
        input=input_text, capture_output=True, text=True,
        cwd=cwd or project_root, env=env)


# -- 3-way differential: VM vs WASM vs native, byte-identical ------------------
_node = shutil.which('node')
_cc = shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')


def _strip_banner(out):
    lines = out.splitlines(keepends=True)
    if lines and lines[0].startswith('✓ built'):
        lines = lines[1:]
    return ''.join(lines)


def _run_vm(src_path):
    r = _niko2('run', str(src_path))
    assert r.returncode == 0, f'VM failed: {(r.stdout + r.stderr)[:600]}'
    return r.stdout


def _run_backend(cmd, src_path):
    r = _niko2(cmd, str(src_path), '--run')
    assert r.returncode == 0, f'{cmd} failed: {(r.stdout + r.stderr)[:600]}'
    return _strip_banner(r.stdout)


def _three_way(name, files, entry_name, entry_src, want):
    """Write `files` + `entry_src` into a fresh dir, run all backends."""
    d = pathlib.Path(tempfile.mkdtemp(prefix='niko-use22-'))
    try:
        for fname, content in files.items():
            p = d / fname
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding='utf8')
        main = d / entry_name
        main.write_text(entry_src, encoding='utf8')
        vm_out = _run_vm(main)
        assert vm_out == want, f'[{name}] VM output mismatch:\n{vm_out!r}\n!=\n{want!r}'
        if _node:
            wasm_out = _run_backend('wasm', main)
            assert wasm_out == vm_out, f'[{name}] WASM != VM:\n{wasm_out!r}\n{vm_out!r}'
        else:
            print('SKIP 3-way WASM: node.js not on PATH')
        if _cc:
            native_out = _run_backend('native', main)
            assert native_out == vm_out, f'[{name}] native != VM:\n{native_out!r}\n{vm_out!r}'
        else:
            print('SKIP 3-way native: no C compiler on PATH')
        _ok(f'3-way {name}')
    finally:
        shutil.rmtree(d, ignore_errors=True)


_three_way(
    'basic-use',
    {'util.niko': 'set greeting to "hello"\n'
                  'to shout with s:\n'
                  '    give back s + "!"\n'},
    'main.niko',
    'use "util.niko"\nsay greeting\nsay shout("hey")\n',
    'hello\nhey!\n')

_three_way(
    'transitive-use',
    {'deep/c.niko': 'set cval to 30\n',
     'b.niko': 'use "deep/c.niko"\nset bval to cval + 10\n'},
    'main.niko',
    'use "b.niko"\nsay bval + cval\n',
    '70\n')

_three_way(
    'diamond-init-once',
    {'shared.niko': 'say "shared init"\nset base to 100\n',
     'l.niko': 'use "shared.niko"\nto f with x:\n    give back x * 2\n',
     'r.niko': 'use "shared.niko"\nto g with x:\n    give back x + 1\n'},
    'main.niko',
    'use "l.niko"\nuse "r.niko"\nsay f(21)\nsay g(f(1))\nsay base\n',
    'shared init\n42\n3\n100\n')

_three_way(
    'conflict-resolution',
    {'c1.niko': 'set who to "c1"\nset shared to "from-c1"\n',
     'c2.niko': 'set who to "c2"\nset shared to "from-c2"\n'},
    'main.niko',
    'use "c1.niko"\nuse "c2.niko"\nsay who\nsay shared\n'
    'set shared to "entry-wins"\nsay shared\n',
    'c2\nfrom-c2\nentry-wins\n')

_three_way(
    'nested-function-in-used-file',
    {'lib.niko': 'set factor to 3\n'
                 'to make_mult:\n'
                 '    to mult with x:\n'
                 '        give back x * factor\n'
                 '    give back mult\n'
                 'set twice3 to make_mult()\n'},
    'main.niko',
    'use "lib.niko"\nsay twice3(7)\n',
    '21\n')

_three_way(
    'builtin-use-all-backends',
    {},
    'main.niko',
    'use "math"\nsay floor(3.7)\nsay abs(-5)\n',
    '3\n5\n')


def test_builtin_wins_over_file():
    # A builtin-name `use` is a no-op even when a same-named file sits
    # next to the entry program (pre-existing Alpha 4 semantics).
    d = pathlib.Path(tempfile.mkdtemp(prefix='niko-use22-'))
    try:
        (d / 'math.niko').write_text('set shadowed to "a-file"\n')
        (d / 'main.niko').write_text('use "math"\nsay floor(3.7)\n')
        vm_out = _run_vm(d / 'main.niko')
        assert vm_out == '3\n', vm_out
        _ok('builtin-use-wins-over-file')
    finally:
        shutil.rmtree(d, ignore_errors=True)


test_builtin_wins_over_file()

# -- error cases: carets, right file, exit nonzero ---------------------------


def _expect_error(name, files, entry_name, needle_lines, entry_src=None):
    d = pathlib.Path(tempfile.mkdtemp(prefix='niko-use22-'))
    try:
        for fname, content in files.items():
            p = d / fname
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding='utf8')
        if entry_src is not None:
            (d / entry_name).write_text(entry_src, encoding='utf8')
        main = d / entry_name
        r = _niko2('run', str(main))
        assert r.returncode != 0, f'[{name}] expected failure, got:\n{r.stdout}'
        for needle in needle_lines:
            assert needle in r.stdout, f'[{name}] missing {needle!r} in:\n{r.stdout}'
        _ok(f'error {name}')
    finally:
        shutil.rmtree(d, ignore_errors=True)


_expect_error(
    'use-cycle',
    {'a.niko': 'use "b.niko"\n', 'b.niko': 'use "a.niko"\n'},
    'a.niko',
    ['use cycle: a.niko -> b.niko -> a.niko', 'line 1', 'use "a.niko"'])

_expect_error(
    'use-self-cycle',
    {'s.niko': 'use "s.niko"\nsay "never"\n'},
    's.niko',
    ['use cycle: s.niko -> s.niko'])

_expect_error(
    'missing-used-file',
    {},
    'main.niko',
    ['cannot find module "missing.niko"', 'use "missing.niko"'],
    entry_src='use "missing.niko"\nsay "never"\n')

_expect_error(
    'import-inside-used-file',
    {'used.niko': 'import "b.niko" as b\nset x to 1\n',
     'b.niko': 'set y to 2\n'},
    'main.niko',
    ["'import' is not supported inside used modules",
     'used.niko', 'import "b.niko" as b'],
    entry_src='use "used.niko"\nsay "never"\n')

_expect_error(
    'use-inside-imported-module',
    {'imp.niko': 'use "b.niko"\nset x to 1\n',
     'b.niko': 'set y to 2\n'},
    'main.niko',
    ["'use' is not supported inside imported modules",
     'imp.niko', 'use "b.niko"'],
    entry_src='import "imp.niko" as m\nsay m.x\n')


_expect_error(
    'use-inside-function',
    {},
    'main.niko',
    ["'use' is only allowed at the top of a file, not inside a function",
     'line 2', 'use "helper.niko"'],
    entry_src='to f:\n    use "helper.niko"\n    say 1\nf()\n')


def test_use_inside_function_checker():
    # Checker-level: `use` inside a function body is rejected with the
    # right line number, mirroring `import`'s existing
    # 'import must be at the top of the file' rule. A top-level `use`
    # still checks clean.
    from niko2.parser import parse
    from niko2.typecheck import check, TypeErrorNiko
    tree = parse('to f:\n    use "x.niko"\n    say 1\n')
    try:
        check(tree, [])
    except TypeErrorNiko as e:
        assert e.line == 2, e.line
        assert "'use' is only allowed at the top of a file" in str(e), str(e)
    else:
        raise AssertionError('expected TypeErrorNiko for use inside a function')
    check(parse('use "x.niko"\nsay 1\n'), [])
    _ok('use-inside-function-checker')


test_use_inside_function_checker()


def test_type_error_attribution():
    # A checker error inside a used file names the used file and its
    # own line, with a caret -- not the entry file.
    d = pathlib.Path(tempfile.mkdtemp(prefix='niko-use22-'))
    try:
        (d / 'used_bad.niko').write_text(
            'set ok to 1\nsay unknown_name_xyz\nset more to 3\n')
        (d / 'main.niko').write_text('use "used_bad.niko"\nsay "never"\n')
        r = _niko2('run', str(d / 'main.niko'))
        assert r.returncode != 0, r.stdout
        assert 'used_bad.niko' in r.stdout, r.stdout
        assert 'line 2' in r.stdout, r.stdout
        assert 'say unknown_name_xyz' in r.stdout, r.stdout
        assert '^^' in r.stdout, r.stdout
        _ok('type-error-attribution')
    finally:
        shutil.rmtree(d, ignore_errors=True)


test_type_error_attribution()

print(f'test_use_pipeline.py: all {PASS} checks passed')
