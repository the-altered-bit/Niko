"""Alpha 11: match guards + list/record patterns.

Checker rejections, malformed-pattern parse errors, and a 3-way differential
(VM/WASM/native byte-identical) over the canonical pattern programs.
Positive VM coverage lives in tests/niko2_cases/match_patterns.niko.
"""
import os, pathlib, shutil, subprocess, sys, tempfile

root = pathlib.Path(__file__).resolve().parent
project_root = root.parent

def niko2(*args, cwd=None, input_text=None):
    env = dict(os.environ, PYTHONPATH=str(project_root))
    return subprocess.run([sys.executable, '-m', 'niko2', *args],
                          capture_output=True, text=True,
                          cwd=cwd or project_root, env=env, input=input_text)

def check_src(src):
    with tempfile.TemporaryDirectory() as tmp:
        p = pathlib.Path(tmp) / 't.niko'
        p.write_text(src)
        return niko2('check', str(p))

# (source, expected error fragment)
bad = [
    # list pattern against a known non-list
    ('match 5:\n    when [a]:\n        say a\n',
     'cannot match [...] against number'),
    ('match "hi":\n    when [a, b]:\n        say a\n',
     'cannot match [...] against text'),
    # record pattern against a known non-record
    ('match 5:\n    when {a: b}:\n        say b\n',
     'cannot match {...} against number'),
    ('match [1]:\n    when {a: b}:\n        say b\n',
     'cannot match {...} against list'),
    # nested: list pattern inside against a known element type
    ('set xs: list<number> to [1]\nmatch xs:\n    when [{a: b}]:\n        say b\n',
     'cannot match {...} against number'),
    # guard must be boolean
    ('match [1]:\n    when [a] if 5:\n        say a\n',
     'when guard must be boolean, got number'),
    ('match [1]:\n    when [a] if "x":\n        say a\n',
     'when guard must be boolean, got text'),
    # guard referencing an unknown name
    ('match [1]:\n    when [a] if zzz is bigger than 1:\n        say a\n',
     'unknown name "zzz"'),
    # malformed patterns
    ('match [1]:\n    when [...a, b]:\n        say 1\n',
     '"...rest" must be the last pattern in [...]'),
    ('match [1]:\n    when [...a, ...b]:\n        say 1\n',
     'only one "...rest" allowed in a list pattern'),
    ('match [1]:\n    when [...99]:\n        say 1\n',
     'bad rest name "99"'),
    ('match [1]:\n    when {name}:\n        say 1\n',
     'bad record pattern "name" -- use key: name'),
    ('match [1]:\n    when {1: x}:\n        say 1\n',
     'bad record pattern key "1"'),
    ('match [1]:\n    when {a:}:\n        say 1\n',
     'needs a pattern after ":"'),
    ('match [1]:\n    when [a] if:\n        say 1\n',
     '"if" in "when" needs a condition'),
    # ok/error still take a bare name, not a sub-pattern
    ('match ok([1]):\n    when ok [a]:\n        say a\n',
     'bad pattern "ok [a]"'),
]

# sources that must still pass the checker
good = [
    'match [1, 2]:\n    when [a, b]:\n        say a\n',
    'match [1]:\n    when []:\n        say "e"\n    otherwise:\n        say "n"\n',
    'match [1, 2, 3]:\n    when [h, ...t]:\n        say h\n',
    'match {a: 1}:\n    when {a: x}:\n        say x\n',
    'match [1]:\n    when [a] if a is bigger than 0:\n        say a\n',
    'match [[1], {x: 2}]:\n    when [[a], {x: b}] if a is b:\n        say a\n',
    'set xs: list<number> to [1]\nmatch xs:\n    when [a]:\n        say a + 1\n',
]

for i, (src, frag) in enumerate(bad):
    r = check_src(src)
    assert r.returncode != 0, f'bad[{i}] unexpectedly passed:\n{src}'
    assert frag in (r.stdout + r.stderr), f'bad[{i}] missing {frag!r}:\n{r.stdout}{r.stderr}'

for i, src in enumerate(good):
    r = check_src(src)
    assert r.returncode == 0, f'good[{i}] unexpectedly failed:\n{src}\n{r.stdout}{r.stderr}'

print('test_match_patterns.py: checker assertions passed')

# -- 3-way differential: VM vs WASM vs native, byte-identical ------------------
MATCH_PATTERN_CASES = [
    ('guards', '''\
match [3, 1]:
    when [a, b] if a is bigger than b:
        say "a wins"
    when [a, b]:
        say "b wins or tie"
match [1, 3]:
    when [a, b] if a is bigger than b:
        say "a wins"
    when [a, b]:
        say "b wins or tie"
'''),
    ('guard_fallthrough', '''\
to describe with v:
    match v:
        when [a] if a is bigger than 10:
            say "big " + text(a)
        when [a]:
            say "small " + text(a)
        otherwise:
            say "not a one-list"
describe([20])
describe([5])
describe("x")
describe(42)
'''),
    ('lists', '''\
match [1, 2]:
    when []:
        say "empty"
    when [a, b]:
        say text(a + b)
match []:
    when []:
        say "empty"
    otherwise:
        say "not empty"
match [[1, 2], 3]:
    when [[a, b], c]:
        say text(a) + text(b) + text(c)
    otherwise:
        say "no"
match [ok(7)]:
    when [ok v]:
        say "ok " + text(v)
    otherwise:
        say "no"
'''),
    ('rest', '''\
match [7, 8, 9, 10]:
    when [first, ...rest]:
        say text(first) + ":" + text(length(rest))
    otherwise:
        say "no"
match [5]:
    when [...all]:
        say "all=" + text(length(all))
match [1]:
    when [first, ...rest]:
        say text(first) + ":" + text(length(rest))
match []:
    when [first, ...rest]:
        say "matched"
    otherwise:
        say "empty falls through"
'''),
    ('records', '''\
match {name: "Casper", age: 30, city: "Kochi"}:
    when {name: n, age: a} if a is at least 18:
        say n + " adult"
    when {name: n}:
        say n + " minor"
    otherwise:
        say "no"
match {name: "Bo"}:
    when {name: n, age: a}:
        say n + " has age"
    when {name: n}:
        say n + " no age"
match {a: [1, 2]}:
    when {a: [x, ...rest]}:
        say text(x) + "+" + text(length(rest))
    otherwise:
        say "no"
match {}:
    when {}:
        say "any record"
    otherwise:
        say "not a record"
to checkrec with v:
    match v:
        when {}:
            say "any record"
        otherwise:
            say "not a record"
checkrec({})
checkrec(5)
'''),
    ('alternatives_and_types', '''\
to classify with x:
    match x:
        when [a, b], {k: v}:
            say "shape"
        otherwise:
            say "other"
classify([1, 2])
classify({k: 9})
classify(42)
classify("hi")
match 1:
    when 1, 2 if no:
        say "never"
    when 1:
        say "one"
'''),
    ('closures_over_patterns', '''\
to adder with n:
    to f with x:
        match [x, n]:
            when [a, b] if b is bigger than 10:
                give back a + b
            otherwise:
                give back 0
    give back f
set g to adder(20)
say g(5)
set h to adder(3)
say h(5)
'''),
]

def run_vm(src):
    return niko2('run', '/dev/stdin', input_text=src)

def _strip_banner(out):
    lines = out.splitlines(keepends=True)
    if lines and lines[0].startswith('✓ built'):
        lines = lines[1:]
    return ''.join(lines)

def _run_backend(cmd, src):
    with tempfile.TemporaryDirectory() as tmp:
        p = pathlib.Path(tmp) / 't.niko'
        p.write_text(src, encoding='utf8')
        r = niko2(cmd, str(p), '--run')
        assert r.returncode == 0, f'{cmd} failed: {(r.stdout + r.stderr)[:500]}'
        return _strip_banner(r.stdout)

_node = shutil.which('node')
_cc = shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')

for name, src in MATCH_PATTERN_CASES:
    r = run_vm(src)
    assert r.returncode == 0, f'case[{name}] VM failed:\n{r.stdout}{r.stderr}'
    vm_out = r.stdout
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

print('test_match_patterns.py: all assertions passed')
