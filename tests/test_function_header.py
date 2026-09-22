#!/usr/bin/env python3
"""Alpha 21 item 1: colon after the function name/params is the block opener,
never part of the identifier.

`to greet: -> text:` used to parse the function name as `greet:` (including
the colon), and `to add with a, b: -> number:` parsed the last param as
`b:`. Both made the header unusable, since `greet:` can never be called.
The parser now drops one trailing colon from the header before the
return-type arrow, i.e. right after the name or the last param.
"""
import pathlib, shutil, subprocess, sys, tempfile

root = pathlib.Path(__file__).resolve().parent
project_root = root.parent
sys.path.insert(0, str(project_root))  # niko2 package (mirrors test_closures.py)

from niko2.parser import parse

def header_of(src):
    fd = parse(src).body[0]
    assert type(fd).__name__ == 'FunctionDef', fd
    return fd.name, fd.params, fd.return_type, fd.line

# -- parser: colon forms -------------------------------------------------------
assert header_of('to greet: -> text:\n    give back "hi"\n') == ('greet', [], 'text', 1)
assert header_of('to add with a, b: -> number:\n    give back a + b\n') == ('add', ['a', 'b'], 'number', 1)
assert header_of('to f with x : -> number:\n    give back x\n') == ('f', ['x'], 'number', 1)
assert header_of('to f with n: number: -> number:\n    give back n\n') == ('f', ['n: number'], 'number', 1)

# -- parser: forms that already worked are unchanged ---------------------------
assert header_of('to greet:\n    give back "hi"\n') == ('greet', [], None, 1)
assert header_of('to add with a, b -> number:\n    give back a + b\n') == ('add', ['a', 'b'], 'number', 1)
assert header_of('to factorial with n: number -> number:\n    give back n\n') == ('factorial', ['n: number'], 'number', 1)
assert header_of('to f with a, b:\n    give back a\n') == ('f', ['a', 'b'], None, 1)

# -- parser: line numbers are preserved through the strip ----------------------
tree = parse('say 1\nto greet: -> text:\n    give back "hi"\n')
assert tree.body[1].line == 2, tree.body[1].line

# -- end-to-end: define and call the colon-header forms -------------------------
def run_vm(src):
    return subprocess.run(
        [sys.executable, '-m', 'niko2', 'run', '/dev/stdin'],
        input=src, capture_output=True, text=True, cwd=project_root)

COLON_CASES = [
    ('colon_after_name', 'to greet: -> text:\n    give back "hi"\nsay greet()\n', 'hi\n'),
    ('colon_after_params', 'to add with a, b: -> number:\n    give back a + b\nsay add(2, 3)\n', '5\n'),
    ('colon_after_name_no_ret', 'to ping:\n    say "pong"\nping()\n', 'pong\n'),
]

for name, src, want in COLON_CASES:
    r = run_vm(src)
    assert r.returncode == 0, f'[{name}] niko2 run failed:\n{r.stdout}\n{r.stderr}'
    assert r.stdout == want, f'[{name}] got {r.stdout!r}, want {want!r}'

# -- 3-way differential: headers feed all backends from the same parse --------
def _strip_banner(out):
    lines = out.splitlines(keepends=True)
    if lines and lines[0].startswith('✓ built'):
        lines = lines[1:]
    return ''.join(lines)

def _run_backend(cmd, src):
    with tempfile.TemporaryDirectory() as tmp:
        p = pathlib.Path(tmp) / 't.niko'
        p.write_text(src, encoding='utf8')
        r = subprocess.run([sys.executable, '-m', 'niko2', cmd, str(p), '--run'],
                           capture_output=True, text=True, cwd=project_root)
        assert r.returncode == 0, f'{cmd} failed: {(r.stdout + r.stderr)[:500]}'
        return _strip_banner(r.stdout)

_node = shutil.which('node')
_cc = shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')

for name, src, want in COLON_CASES:
    vm_out = run_vm(src).stdout
    if _node:
        wasm_out = _run_backend('wasm', src)
        assert wasm_out == vm_out, f'3-way[{name}] WASM != VM:\n{wasm_out!r}\n{vm_out!r}'
    else:
        print('SKIP 3-way WASM: node.js not on PATH')
    if _cc:
        native_out = _run_backend('native', src)
        assert native_out == vm_out, f'3-way[{name}] native != VM:\n{native_out!r}\n{native_out!r}'
    else:
        print('SKIP 3-way native: no C compiler on PATH')
print('3-way differential (VM/WASM/native): all matched')

print('test_function_header.py: all assertions passed')
