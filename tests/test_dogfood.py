#!/usr/bin/env python3
"""Alpha 28 dogfood fixes: three language bugs found while building niko-ssg
(a static site generator written in Niko, examples/ssg/).

1. Parser: sequential `if` statements were swallowed as one if/elif chain.
   `parse_if`'s continuation loop matched any later `if ` line, so the
   second body never ran when the first condition was true. Also broke an
   `if` block whose last statement was a nested `if` ("expected an
   indented block"). Fixed in niko2/parser.py: only `otherwise if` /
   `otherwise:` at the same indent continue the statement.

2. Modules: forward references failed inside imported modules.
   `check_units` pre-defined the entry script's own top-level names but not
   a dependency module's, so a function could not call another defined
   later in the same module file. Fixed in niko2/modules.py: every unit
   pre-defines its own top-level names.

3. Closures: a nested closure reading a builtin-*named* variable bound by
   an enclosing function got the builtin instead of the binding (e.g. an
   `import ... as text` alias used inside a module function resolved to the
   `text()` builtin). `closures.py` excluded builtin names from capture
   unconditionally. Fixed: a builtin name bound by an enclosing function
   is captured like any other local.

All cases run 3-way differential (VM/WASM/native): byte-identical output.
"""
import pathlib
import shutil
import subprocess
import sys
import tempfile

root = pathlib.Path(__file__).resolve().parent
project_root = root.parent
sys.path.insert(0, str(project_root))

import os

# (name, {filename: source}, entry, expected stdout)
CASES = [
    # --- 1. parser: sequential ifs -------------------------------------
    ('seq_ifs', {'t.niko': '''\
set a to yes
set b to yes
if a:
    say "A"
if b:
    say "B"
'''}, 't.niko', 'A\nB\n'),
    ('seq_ifs_first_false', {'t.niko': '''\
if no:
    say "A"
if yes:
    say "B"
'''}, 't.niko', 'B\n'),
    ('nested_if_last_in_block', {'t.niko': '''\
set n to 1
if n is 1:
    if n is 1:
        set n to 10
if n is 10:
    say "ten"
say n
'''}, 't.niko', 'ten\n10\n'),
    ('if_otherwise_if_chain', {'t.niko': '''\
set x to 2
if x is 1:
    say "one"
otherwise if x is 2:
    say "two"
otherwise:
    say "other"
'''}, 't.niko', 'two\n'),
    ('if_otherwise_only', {'t.niko': '''\
if no:
    say "A"
otherwise:
    say "B"
'''}, 't.niko', 'B\n'),
    # --- 2. closures: builtin shadowing --------------------------------
    ('shadow_text_read', {'t.niko': '''\
to outer:
    set text to "shadowed"
    to inner:
        give back text
    give back inner()
say outer()
'''}, 't.niko', 'shadowed\n'),
    ('shadow_length_read', {'t.niko': '''\
to outer:
    set length to "my-length"
    to inner:
        give back length
    give back inner()
say outer()
'''}, 't.niko', 'my-length\n'),
    ('shadow_write_through', {'t.niko': '''\
to outer:
    set text to "a"
    to inner:
        set text to text + "b"
        give back text
    give back inner()
say outer()
'''}, 't.niko', 'ab\n'),
    ('shadow_direct_call_still_builtin', {'t.niko': '''\
to outer:
    set text to "shadow"
    to inner:
        give back text(42)
    give back inner()
say outer()
'''}, 't.niko', '42\n'),
    # --- 3. modules: forward references --------------------------------
    ('module_forward_ref', {
        'm.niko': '''\
to aaa:
    give back bbb() + 1
to bbb:
    give back 41
''',
        'main.niko': '''\
import "m.niko" as m
say m.aaa()
''',
    }, 'main.niko', '42\n'),
    ('module_mutual_recursion', {
        'm.niko': '''\
to is_even with n:
    if n is 0:
        give back yes
    give back is_odd(n - 1)
to is_odd with n:
    if n is 0:
        give back no
    give back is_even(n - 1)
''',
        'main.niko': '''\
import "m.niko" as m
say m.is_even(10)
say m.is_odd(7)
''',
    }, 'main.niko', 'yes\nyes\n'),
    ('module_import_alias_shadows_builtin', {
        'pages.niko': '''\
import "stdlib/text.niko" as text
to slug_for with path:
    give back text.slugify(path)
''',
        'main.niko': '''\
import "pages.niko" as pages
say pages.slug_for("Hello World")
''',
    }, 'main.niko', 'hello-world\n'),
]


def _niko2(*args, cwd):
    env = dict(os.environ)
    env['PYTHONPATH'] = str(project_root)
    return subprocess.run(
        [sys.executable, '-m', 'niko2', *args],
        capture_output=True, text=True, cwd=cwd, env=env)


def _strip_banner(out):
    lines = out.splitlines(keepends=True)
    if lines and lines[0].startswith('\u2713 built'):
        lines = lines[1:]
    return ''.join(lines)


node = shutil.which('node')
cc = shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')

for name, files, entry, want in CASES:
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        for fname, src in files.items():
            p = tmp / fname
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(src, encoding='utf8')
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

print('test_dogfood.py: all assertions passed')
