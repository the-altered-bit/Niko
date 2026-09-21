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
sys.path.insert(0, str(root))            # niko2 package
sys.path.insert(0, str(root / 'tests'))  # sibling test modules
from test_closures import CLOSURE_CASES
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

def test_wasm_closures():
    # Alpha 10: the canonical closure programs from tests/test_closures.py,
    # run through the WASM backend and compared byte-for-byte with the
    # expected (VM-verified) output.
    if not node:
        print('SKIP: node.js not on PATH')
        return
    for name, src, expected in CLOSURE_CASES:
        got = run_wasm(src)
        assert got == expected, (
            f'closure mismatch for {name}:\nWASM: {got!r}\nexpected: {expected!r}')
        print(f'ok: closure/{name}')

def run_wasm_raw(src):
    # like run_wasm but returns the CompletedProcess (for runtime-error cases)
    with tempfile.TemporaryDirectory() as tmp:
        p = pathlib.Path(tmp) / 't.niko'
        p.write_text(src, encoding='utf8')
        return niko2('wasm', str(p), '--run')

def test_wasm_call_errors():
    # Alpha 10: runtime errors for calls through values keep the VM's wording.
    if not node:
        print('SKIP: node.js not on PATH')
        return
    r = run_wasm_raw('set r to ok(5)\nset v to unwrap(r)\nsay v(1)\n')
    assert r.returncode != 0, 'calling a non-function should fail'
    assert "I can't call 5 as a function." in r.stderr, r.stderr[:500]
    print('ok: call_non_function')
    r = run_wasm_raw('to add with a, b:\n    give back a + b\nsay add(1)\n')
    assert r.returncode != 0, 'arity mismatch should fail'
    assert 'add expected 2 arguments, got 1.' in r.stderr, r.stderr[:500]
    print('ok: arity_mismatch')
    # the checker still rejects builtins as values before the backend runs
    r = run_wasm_raw('set f to length\n')
    assert r.returncode != 0, 'builtin-as-value should fail'
    assert 'can\'t use the builtin "length" as a value' in (r.stdout + r.stderr)
    print('ok: builtin_as_value_rejected')


STRESS_SRC = """\
say "hello"
set x to 1 + 2 * 3
say x
say 4.5
say 10 / 4
say 2 ** 8
say yes
say no
say nothing
set name to "world"
say "hi " + name
say length("hello")
say upper("abc")
say 1 is smaller than 2
say 5 is 5
if x is 7:
    say "seven"
otherwise:
    say "not seven"
set total to 0
for each i in [1, 2, 3, 4, 5]:
    set total to total + i
say total
set r to {"a": 1, "b": 2}
say r["a"]
say r.a
to add with a: number, b: number -> number:
    give back a + b
say add(3, 4)
say sorted([3, 1, 2])
say reversed("abc")
say 1 + 2 is 3
set n to 0
while n is smaller than 3:
    say n
    set n to n + 1
"""

STRESS_EXPECTED = """\
hello
7
4.5
2.5
256
yes
no
nothing
hi world
5
ABC
yes
yes
seven
15
1
1
7
[1, 2, 3]
["c", "b", "a"]
yes
0
1
2
"""

def test_wasm_stress_correctness():
    # WASM-only (not differential): this program mixes yes/no with 1/0
    # literals, which trips the VM's known constant-pool dedup bug
    # (True == 1, False == 0 in `constants.index`). The WASM backend
    # prints the correct values: `say yes` -> "yes", `say n` (n = 0) -> "0".
    # Added 2026-09-21 after a 36-line stress program was verified
    # output-by-output against the VM.
    if not node:
        print('SKIP: node.js not on PATH')
        return
    got = run_wasm(STRESS_SRC)
    assert got == STRESS_EXPECTED, f'stress mismatch:\nWASM: {got!r}\nexpected: {STRESS_EXPECTED!r}'
    print('ok: stress_correctness')

if __name__ == '__main__':
    test_wasm_differential()
    test_wasm_closures()
    test_wasm_call_errors()
    test_wasm_stress_correctness()
    print('all wasm tests passed')
