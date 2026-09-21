"""Alpha 8: differential tests — WASM backend output vs VM output.

Skips if node.js is not on PATH.

Note: the VM has a known constant-pool bug (2026-09-21): Python
True == 1 and False == 0, so `constants.index(v)` can deduplicate
`yes` with `1` and `no` with `0`, printing the wrong one when both
appear in a program. The WASM backend is correct. Test programs avoid
mixing yes/no with 1/0 literals to keep the VM output canonical.
"""
import os, pathlib, shutil, subprocess, sys, tempfile

root = pathlib.Path(__file__).resolve().parent.parent
node = shutil.which('node')

def niko2(*args, cwd=None, input_text=None):
    env = dict(os.environ, PYTHONPATH=str(root))
    return subprocess.run([sys.executable, '-m', 'niko2', *args],
                          capture_output=True, text=True,
                          cwd=cwd or root, env=env, input=input_text)

def run_vm(src):
    with tempfile.TemporaryDirectory() as tmp:
        p = pathlib.Path(tmp) / 't.niko'
        p.write_text(src, encoding='utf8')
        r = niko2('run', str(p))
        assert r.returncode == 0, f'VM failed: {r.stderr[:300]}'
        return r.stdout

def run_wasm(src):
    with tempfile.TemporaryDirectory() as tmp:
        p = pathlib.Path(tmp) / 't.niko'
        p.write_text(src, encoding='utf8')
        r = niko2('wasm', str(p), '--run')
        assert r.returncode == 0, f'WASM failed: {r.stderr[:300]}'
        # strip the "✓ built ..." line
        lines = r.stdout.splitlines(keepends=True)
        if lines and lines[0].startswith('✓ built'):
            lines = lines[1:]
        return ''.join(lines)

def assert_same(src):
    w = run_wasm(src)
    v = run_vm(src)
    assert w == v, f'mismatch for:\n{src}\nVM: {v!r}\nWASM: {w!r}'

CASES = [
    ('say_text_number', 'say "hello"\nsay 42\nsay 4.5\n'),
    ('arithmetic', 'say 1 + 2 * 3\nsay 10 / 4\nsay 2 ** 8\nsay 7 % 3\n'),
    ('text_ops', 'set name to "world"\nsay "hi " + name\nsay length("hello")\nsay upper("abc")\n'),
    ('yesno_nothing', 'say yes\nsay no\nsay nothing\n'),
    ('comparison', 'say 1 is smaller than 2\nsay 5 is 5\nsay 3 is bigger than 4\n'),
    ('if_otherwise', 'set x to 7\nif x is 7:\n    say "seven"\notherwise:\n    say "not seven"\n'),
    ('for_loop', 'set total to 0\nfor each i in [1, 2, 3]:\n    set total to total + i\nsay total\n'),
    ('while_loop', 'set n to 0\nwhile n is smaller than 3:\n    say n\n    set n to n + 1\n'),
    ('list_ops', 'say [3, 1, 2]\nsay sorted([3, 1, 2])\nsay reversed("abc")\n'),
    ('record', 'set r to {"a": 1, "b": 2}\nsay r["a"]\nsay r.a\n'),
    ('function', 'to add with a: number, b: number -> number:\n    give back a + b\nsay add(3, 4)\n'),
    ('string_in_list_quoted', 'say ["c", "b", "a"]\n'),
]

def test_wasm_differential():
    if not node:
        print('SKIP: node.js not on PATH')
        return
    for name, src in CASES:
        assert_same(src)
        print(f'ok: {name}')

def test_wasm_unsupported_closure():
    # closures over enclosing function locals are a parity gap -> CompileError
    sys.path.insert(0, str(root))
    from niko2.parser import parse
    from niko2.typecheck import check
    from niko2.backends.wasm import WasmBackend, CompileError
    src = 'to outer:\n    set x to 1\n    to inner:\n        say x\n'
    tree = parse(src)
    check(tree, [])
    try:
        WasmBackend().compile(tree)
    except CompileError:
        print('ok: closure rejected')
        return
    raise AssertionError('expected CompileError for closure')

if __name__ == '__main__':
    test_wasm_differential()
    test_wasm_unsupported_closure()
    print('all wasm tests passed')
