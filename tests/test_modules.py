#!/usr/bin/env python3
"""Alpha 13: modules -- `import "path/to/file.niko" as alias`.

MODULE_CASES: multi-file programs ({filename: source} + entry file), run
3-way differential (VM/WASM/native): byte-identical output. WASM and native
runs skip cleanly when node/cc are missing.

ERROR_CASES: import-time failures. The frontend (parser/checker/modules) is
shared by every backend, so the diagnostic is asserted on all available
backends and the exit code must be nonzero everywhere.

Also covers: the formatter's ImportStmt rendering, and the LSP keyword list.
"""
import pathlib
import shutil
import subprocess
import sys
import tempfile

root = pathlib.Path(__file__).resolve().parent
project_root = root.parent
sys.path.insert(0, str(project_root))

MATHUTIL = '''\
to add with x, y:
    give back x + y
to double with x:
    give back x * 2
set factor to 10
'''

MODULE_CASES = [
    ('basic', {
        'mathutil.niko': MATHUTIL,
        'app.niko': 'import "mathutil.niko" as m\n'
                    'say m.add(2, 3)\n'
                    'say m.double(21)\n'
                    'say m.factor\n',
    }, 'app.niko', '5\n42\n10\n'),

    # A module's top-level code runs exactly once, no matter how many
    # aliases or transitive importers it has.
    ('cache_once', {
        'base.niko': 'say "base loaded"\n'
                     'to who -> text:\n'
                     '    give back "base"\n',
        'mid.niko': 'import "base.niko" as b\n'
                    'say "mid loaded"\n'
                    'to greet -> text:\n'
                    '    give back b.who() + " via mid"\n',
        'top.niko': 'import "base.niko" as b1\n'
                    'import "mid.niko" as m\n'
                    'import "base.niko" as b2\n'
                    'say m.greet()\n'
                    'say b1.who()\n'
                    'say b2.who()\n',
    }, 'top.niko', 'base loaded\nmid loaded\nbase via mid\nbase\nbase\n'),

    # Closures survive the module boundary (Alpha 10 interplay): each call
    # to the exported factory gets its own captured cell.
    ('closure_export', {
        'counter.niko': 'to make with start:\n'
                        '    set n to start\n'
                        '    to bump:\n'
                        '        set n to n + 1\n'
                        '        give back n\n'
                        '    give back bump\n',
        'app.niko': 'import "counter.niko" as c\n'
                    'set b to c.make(10)\n'
                    'say b()\n'
                    'say b()\n'
                    'set d to c.make(100)\n'
                    'say d()\n'
                    'say b()\n',
    }, 'app.niko', '11\n12\n101\n13\n'),

    # Non-function exports: lists and records, with indexing/attributes.
    ('data_exports', {
        'data.niko': 'set nums to [1, 2, 3]\n'
                     'set cfg to {host: "localhost", port: 8080}\n',
        'app.niko': 'import "data.niko" as d\n'
                    'say d.nums[2]\n'
                    'say d.cfg.host\n'
                    'say d.cfg.port\n',
    }, 'app.niko', '2\nlocalhost\n8080\n'),

    # A module can use match-as-an-expression internally (Alpha 12 interplay).
    ('match_in_module', {
        'classify.niko': 'to kind with n:\n'
                         '    give back match n:\n'
                         '        when 0:\n'
                         '            "zero"\n'
                         '        when x:\n'
                         '            "nonzero"\n',
        'app.niko': 'import "classify.niko" as c\n'
                    'say c.kind(0)\n'
                    'say c.kind(5)\n',
    }, 'app.niko', 'zero\nnonzero\n'),

    # Imports resolve relative to the importing file's directory.
    ('subdir', {
        'lib/util.niko': 'to shout with s:\n'
                         '    give back upper(s)\n',
        'app.niko': 'import "lib/util.niko" as u\n'
                    'say u.shout("hello")\n',
    }, 'app.niko', 'HELLO\n'),

    # An explicit ./ prefix resolves the same way.
    ('dot_prefix', {
        'mathutil.niko': MATHUTIL,
        'app.niko': 'import "./mathutil.niko" as m\n'
                    'say m.add(1, 2)\n',
    }, 'app.niko', '3\n'),
]

ERROR_CASES = [
    ('cycle',
     {'cyc1.niko': 'import "cyc2.niko" as c\nsay 1\n',
      'cyc2.niko': 'import "cyc1.niko" as c\nsay 2\n'},
     'cyc1.niko', 'import cycle'),

    ('missing',
     {'app.niko': 'say 1\nimport "nope.niko" as n\n'},
     'app.niko', 'cannot find module "nope.niko"'),

    ('bad_suffix',
     {'app.niko': 'import "notes.txt" as n\n'},
     'app.niko', 'import expects a .niko file'),

    ('use_in_module',
     {'m.niko': 'use "x"\n',
      'app.niko': 'import "m.niko" as u\nsay 1\n'},
     'app.niko', "'use' is not supported inside imported modules"),

    ('import_in_function',
     {'m.niko': 'say 1\n',
      'app.niko': 'to f:\n    import "m.niko" as m\n'},
     'app.niko', 'import must be at the top of the file'),

    ('missing_alias',
     {'app.niko': 'import "m.niko" as\n'},
     'app.niko', 'expected as ALIAS'),

    # Checker errors inside a module are reported against the module file.
    ('module_type_error',
     {'bad.niko': 'set x: number to "nope"\n',
      'app.niko': 'import "bad.niko" as b\nsay 1\n'},
     'app.niko', 'bad.niko'),
]


def _niko2(*args, cwd):
    env = dict(__import__('os').environ)
    env['PYTHONPATH'] = str(project_root)
    return subprocess.run(
        [sys.executable, '-m', 'niko2', *args],
        capture_output=True, text=True, cwd=cwd, env=env)


def _strip_banner(out):
    lines = out.splitlines(keepends=True)
    if lines and lines[0].startswith('\u2713 built'):
        lines = lines[1:]
    return ''.join(lines)


def _write_case(tmp, files):
    for name, src in files.items():
        p = tmp / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src, encoding='utf8')


node = shutil.which('node')
cc = shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')

for name, files, entry, want in MODULE_CASES:
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_case(tmp, files)
        r = _niko2('run', entry, cwd=tmp)
        assert r.returncode == 0, f'vm[{name}] failed:\n{r.stdout}{r.stderr}'
        assert r.stdout == want, \
            f'vm[{name}] mismatch:\n{r.stdout!r} != {want!r}'
        vm_out = r.stdout
        if node:
            r = _niko2('wasm', entry, '--run', cwd=tmp)
            assert r.returncode == 0, f'wasm[{name}] failed:\n{r.stdout}{r.stderr}'
            wasm_out = _strip_banner(r.stdout)
            assert wasm_out == vm_out, \
                f'3-way[{name}] WASM != VM:\n{wasm_out!r}\n{vm_out!r}'
        else:
            print('SKIP 3-way WASM: node.js not on PATH')
        if cc:
            r = _niko2('native', entry, '--run', cwd=tmp)
            assert r.returncode == 0, f'native[{name}] failed:\n{r.stdout}{r.stderr}'
            native_out = _strip_banner(r.stdout)
            assert native_out == vm_out, \
                f'3-way[{name}] native != VM:\n{native_out!r}\n{vm_out!r}'
        else:
            print('SKIP 3-way native: no C compiler on PATH')
    print(f'ok: {name}')

for name, files, entry, want_msg in ERROR_CASES:
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        _write_case(tmp, files)
        r = _niko2('run', entry, cwd=tmp)
        assert r.returncode != 0, f'error[{name}] unexpectedly succeeded'
        assert want_msg in r.stdout, \
            f'error[{name}] message missing:\n{r.stdout!r}'
        if node:
            r = _niko2('wasm', entry, cwd=tmp)
            assert r.returncode != 0, f'error[{name}] wasm unexpectedly succeeded'
            assert want_msg in r.stdout, \
                f'error[{name}] wasm message missing:\n{r.stdout!r}'
        if cc:
            r = _niko2('native', entry, cwd=tmp)
            assert r.returncode != 0, f'error[{name}] native unexpectedly succeeded'
            assert want_msg in r.stdout, \
                f'error[{name}] native message missing:\n{r.stdout!r}'
    print(f'ok: error/{name}')

# -- formatter: import statements round-trip --------------------------------
from niko2.parser import parse
from niko2.formatter import format_program

formatted = format_program(parse('import "lib/util.niko" as u\nsay u.shout("x")\n'))
assert 'import "lib/util.niko" as u' in formatted, f'formatter dropped import:\n{formatted}'
print('ok: formatter/import')

# -- LSP offers import/as as keywords ---------------------------------------
from niko2.lsp import KEYWORDS

assert 'import' in KEYWORDS and 'as' in KEYWORDS, 'LSP KEYWORDS missing import/as'
print('ok: lsp keywords')

print('test_modules.py: all assertions passed')
