#!/usr/bin/env python3
"""Alpha 10: closures + first-class functions.

CLOSURE_CASES is the canonical list of closure programs. The WASM and
native backend tests import it and run the same programs through their own
backends, asserting byte-identical output (differential testing).
"""
import pathlib, subprocess, sys, tempfile

root = pathlib.Path(__file__).resolve().parent
project_root = root.parent
sys.path.insert(0, str(project_root))  # niko2 package (mirrors test_wasm.py)

CLOSURE_CASES = [
    ('counter', '''\
to counter with start:
    set n to start
    to bump:
        set n to n + 1
        give back n
    give back bump
set c to counter(10)
say c()
say c()
set d to counter(100)
say d()
say c()
''', '11\n12\n101\n13\n'),
    ('first_class', '''\
to add with a, b:
    give back a + b
set f to add
say f(2, 3)
say f
to apply with g, x:
    give back g(x, 1)
say apply(add, 10)
to make_adder with n:
    to adder with x:
        give back x + n
    give back adder
set add5 to make_adder(5)
say add5(10)
say add5(20)
''', '5\nfunction "add"\n11\n15\n25\n'),
    ('write_through', '''\
to box:
    set v to 0
    to setv with x:
        set v to x
    to getv:
        give back v
    give back [setv, getv]
set pair to box()
set setter to pair[1]
set getter to pair[2]
setter(42)
say getter()
''', '42\n'),
    ('pass_through', '''\
to a:
    set x to 1
    to b:
        to c:
            set x to x + 10
            give back x
        give back c()
    give back b()
say a()
''', '11\n'),
    ('recursion_through_closure', '''\
to outer:
    to fact with n:
        if n <= 1:
            give back 1
        give back n * fact(n - 1)
    give back fact(5)
say outer()
''', '120\n'),
    ('nested_sibling_capture', '''\
to g:
    to h:
        give back 7
    to k:
        give back h()
    give back k()
say g()
''', '7\n'),
    ('loop_shares_variable', '''\
to placeholder:
    give back 0
to f:
    set g1 to placeholder
    set g3 to placeholder
    for each i in [1, 2, 3]:
        to g:
            give back i
        if i is 1:
            set g1 to g
        if i is 3:
            set g3 to g
    give back g1() + g3()
say f()
''', '6\n'),
    ('nested_say', '''\
to outer:
    to inner:
        give back 1
    say inner
    give back inner()
say outer()
''', 'function "inner"\n1\n'),
]


def run_vm(src, input_text=''):
    return subprocess.run(
        [sys.executable, '-m', 'niko2', 'run', '/dev/stdin'],
        input=src if input_text == '' else input_text,
        capture_output=True, text=True, cwd=project_root,
    )


# -- the VM produces the canonical output for every case ---------------------
for name, src, want in CLOSURE_CASES:
    r = run_vm(src)
    assert r.returncode == 0, f'vm[{name}] failed:\n{r.stdout}{r.stderr}'
    assert r.stdout == want, f'vm[{name}] output mismatch:\n{r.stdout!r} != {want!r}'

# -- checker: builtins are not values ----------------------------------------
builtin_value_cases = [
    ('set f to length\n', 'can\'t use the builtin "length" as a value'),
    ('say text\n', 'can\'t use the builtin "text" as a value'),
    ('set g to upper\n', 'can\'t use the builtin "upper" as a value'),
    ('set fs to [length]\n', 'can\'t use the builtin "length" as a value'),
]
for src, frag in builtin_value_cases:
    r = run_vm(src)
    assert r.returncode != 0, f'builtin-as-value unexpectedly passed: {src!r}'
    assert frag in (r.stdout + r.stderr), f'missing {frag!r}:\n{r.stdout}{r.stderr}'

# -- checker: builtins still callable directly, pi still a value --------------
ok_cases = [
    'say length("abcd")\n',
    'say pi\n',
    'set text to "shadow"\nsay text\n',
    'set r to ok("hi")\nmatch r:\n    when ok text:\n        say text\n',
]
for src in ok_cases:
    r = run_vm(src)
    assert r.returncode == 0, f'should pass but failed: {src!r}\n{r.stdout}{r.stderr}'

# -- Alpha 14: a builtin name in direct call position always means the
# builtin, on every backend (checker + WASM/native rule). A user binding
# that shadows the name (here `to length`) is invisible to the call, so
# this is a builtin call `length(9)` and fails at runtime.
r = run_vm('to length with x:\n    give back x\nsay length(9)\n')
assert r.returncode != 0, 'shadowed builtin call should hit the builtin'
assert 'no len()' in (r.stdout + r.stderr) or "can't get the length" in (r.stdout + r.stderr), r.stdout + r.stderr

# -- statically-known non-callables are still rejected by the checker ---------
r = run_vm('set x to 5\nsay x(1)\n')
assert r.returncode != 0 and 'value is not callable' in (r.stdout + r.stderr), r.stdout + r.stderr

# -- runtime: calling a non-function value ------------------------------------
r = run_vm('set r to ok(5)\nset v to unwrap(r)\nsay v(1)\n')
assert r.returncode != 0, 'calling a non-function should fail'
assert "I can't call 5 as a function." in (r.stdout + r.stderr), r.stdout + r.stderr

# -- runtime: arity errors keep their message ----------------------------------
r = run_vm('to add with a, b:\n    give back a + b\nsay add(1)\n')
assert r.returncode != 0 and 'add expected 2 arguments, got 1.' in (r.stdout + r.stderr), r.stdout + r.stderr

# -- closure analysis units ----------------------------------------------------
from niko2.parser import parse
from niko2.typecheck import check
from niko2.closures import analyze_closures

tree = parse('''\
to counter with start:
    set n to start
    to bump:
        set n to n + 1
        give back n
    give back bump
to a:
    set x to 1
    to b:
        to c:
            set x to x + 10
            give back x
        give back c()
    give back b()
''')
check(tree, [])
by_qual = {i.qualname: i for i in analyze_closures(tree).values()}
assert by_qual['counter'].captures == ()
assert by_qual['counter'].boxes == ('n',)
assert by_qual['counter$bump'].captures == ('n',)
assert by_qual['a$b$c'].captures == ('x',)
assert by_qual['a$b'].captures == ('x',)      # pass-through
assert by_qual['a'].boxes == ('x',)
assert by_qual['counter$bump'].nested and not by_qual['counter'].nested

# nested defs with the same name in different parents get unique qualnames
tree2 = parse('to p:\n    to q:\n        give back 1\n    give back q()\nto r:\n    to q:\n        give back 2\n    give back q()\nsay p()\nsay r()\n')
check(tree2, [])
quals = sorted(i.qualname for i in analyze_closures(tree2).values())
assert quals == ['p', 'p$q', 'r', 'r$q'], quals

# -- .nikoir round trip with nested functions ----------------------------------
with tempfile.TemporaryDirectory() as tmp:
    src_file = pathlib.Path(tmp) / 'closures.niko'
    src_file.write_text((root / 'niko2_cases' / 'closures.niko').read_text())
    b = subprocess.run([sys.executable, '-m', 'niko2', 'build', str(src_file)],
                       capture_output=True, text=True, cwd=project_root)
    assert b.returncode == 0, b.stdout + b.stderr
    art = src_file.with_suffix('.nikoir')
    src_file.unlink()
    r = subprocess.run([sys.executable, '-m', 'niko2', 'run', str(art)],
                       capture_output=True, text=True, cwd=project_root)
    want = (root / 'niko2_cases' / 'closures.out').read_text()
    assert r.returncode == 0 and r.stdout == want, r.stdout + r.stderr

# -- 3-way differential: VM vs WASM vs native, byte-identical ------------------
import os as _os, shutil as _shutil

def _niko2(*args, input_text=None):
    env = dict(_os.environ, PYTHONPATH=str(project_root))
    return subprocess.run(
        [sys.executable, '-m', 'niko2', *args],
        input=input_text, capture_output=True, text=True,
        cwd=project_root, env=env)

def _strip_banner(out):
    lines = out.splitlines(keepends=True)
    if lines and lines[0].startswith('✓ built'):
        lines = lines[1:]
    return ''.join(lines)

def _run_backend(cmd, src):
    with tempfile.TemporaryDirectory() as tmp:
        p = pathlib.Path(tmp) / 't.niko'
        p.write_text(src, encoding='utf8')
        r = _niko2(cmd, str(p), '--run')
        assert r.returncode == 0, f'{cmd} failed: {(r.stdout + r.stderr)[:500]}'
        return _strip_banner(r.stdout)

_node = _shutil.which('node')
_cc = _shutil.which('cc') or _shutil.which('gcc') or _shutil.which('clang')

for name, src, want in CLOSURE_CASES:
    vm_out = run_vm(src).stdout
    assert vm_out == want
    if _node:
        wasm_out = _run_backend('wasm', src)
        assert wasm_out == vm_out, f'3-way[{name}] WASM != VM:\n{wasm_out!r}\n{vm_out!r}'
    else:
        print('SKIP 3-way WASM: node.js not on PATH')
    if _cc:
        native_out = _run_backend('native', src)
        assert native_out == vm_out, f'3-way[{name}] native != VM:\n{native_out!r}\n{vm_out!r}'
    else:
        print('SKIP 3-way native: no C compiler on PATH')
print('3-way differential (VM/WASM/native): all matched')

print('test_closures.py: all assertions passed')
