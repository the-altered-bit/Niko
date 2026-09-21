"""Alpha 12: match as an expression.

Checker rejections (exhaustiveness, arm-value shape, arm-type agreement) and
a 3-way differential (VM/WASM/native byte-identical) over the canonical
match-expression programs. Positive VM coverage lives in
tests/niko2_cases/match_expr.niko.
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
    # not exhaustive: no otherwise, last arm is a literal
    ('set x to match 1:\n    when 1:\n        "one"\n',
     'must be exhaustive'),
    # not exhaustive: last arm is a guarded catch-all
    ('set x to match 1:\n    when 1:\n        "one"\n    when y if y is bigger than 0:\n        "big"\n',
     'must be exhaustive'),
    # not exhaustive in give-back position
    ('to f with v:\n    give back match v:\n        when 1:\n            "one"\n',
     'must be exhaustive'),
    # arm body must end with an expression to produce a value
    ('set x to match 1:\n    when 1:\n        say "one"\n    otherwise:\n        "other"\n',
     'must end with an expression'),
    ('set x to match 1:\n    when 1:\n        "one"\n    otherwise:\n        say "other"\n',
     'must end with an expression'),
    # arms must agree on a result type
    ('set x to match 1:\n    when 1:\n        "one"\n    otherwise:\n        2\n',
     'different types'),
    ('set x to match 1:\n    when 1:\n        "one"\n    when y:\n        2\n',
     'different types'),
]

# sources that must still pass the checker
good = [
    # otherwise arm: exhaustive
    'set x to match 1:\n    when 1:\n        "one"\n    otherwise:\n        "other"\n',
    # bare catch-all as the last arm: exhaustive without otherwise
    'set x to match 1:\n    when 1:\n        "one"\n    when y:\n        "other " + text(y)\n',
    # statements before the final expression are fine
    'set x to match [1, 2]:\n    when [a, b]:\n        say "saw"\n        a + b\n    otherwise:\n        0\n',
    # nested match expressions
    'set x to match 1:\n    when 1:\n        set y to match 2:\n            when 2:\n                "in"\n            otherwise:\n                "out"\n        "got " + y\n    otherwise:\n        "no"\n',
    # say position
    'say match 1:\n    when 1:\n        "one"\n    otherwise:\n        "other"\n',
    # give-back position
    'to f with v:\n    give back match v:\n        when 1:\n            "one"\n        otherwise:\n            "other"\n',
    # annotated set target
    'set x: text to match 1:\n    when 1:\n        "one"\n    otherwise:\n        "other"\n',
    # guards + list/record patterns in value position
    'set x to match {n: 5}:\n    when {n: v} if v is bigger than 3:\n        "big"\n    otherwise:\n        "small"\n',
]

for i, (src, frag) in enumerate(bad):
    r = check_src(src)
    assert r.returncode != 0, f'bad[{i}] unexpectedly passed:\n{src}'
    assert frag in (r.stdout + r.stderr), f'bad[{i}] missing {frag!r}:\n{r.stdout}{r.stderr}'

for i, src in enumerate(good):
    r = check_src(src)
    assert r.returncode == 0, f'good[{i}] unexpectedly failed:\n{src}\n{r.stdout}{r.stderr}'

print('test_match_expr.py: checker assertions passed')

# -- 3-way differential: VM vs WASM vs native, byte-identical ------------------
MATCH_EXPR_CASES = [
    ('values', '''\
set grade to match 85:
    when n if n is at least 90:
        "honors"
    when n if n is at least 50:
        "pass"
    otherwise:
        "fail"
say grade
set total to match [3, 4]:
    when [a, b]:
        a + b
    otherwise:
        0
say total
set who to match {name: "Casper"}:
    when {name: n}:
        n
    otherwise:
        "stranger"
say who
'''),
    ('catchall', '''\
set kind to match 7:
    when 1:
        "one"
    when x:
        "other " + text(x)
say kind
set k2 to match 1:
    when 1:
        "one"
    when x:
        "other"
say k2
'''),
    ('positions', '''\
to classify with v:
    give back match v:
        when [a]:
            "single " + text(a)
        when {k: w}:
            "rec " + text(w)
        otherwise:
            "misc"
say classify([9])
say classify({k: 1})
say classify(5)
say match 2:
    when 1:
        "one"
    when 2:
        "two"
    otherwise:
        "many"
'''),
    ('nested', '''\
set nested to match 1:
    when 1:
        set inner to match 2:
            when 2:
                "inner-two"
            otherwise:
                "inner-other"
        "outer-" + inner
    otherwise:
        "outer-other"
say nested
set n2 to match 9:
    when 1:
        "one"
    otherwise:
        set m to match 3:
            when 3:
                "three"
            otherwise:
                "?"
        "got " + m
say n2
'''),
    ('stmts_and_guards', '''\
set v to match [1, 2, 3]:
    when [first, ...rest] if first is 1:
        say "saw rest"
        text(first) + "/" + text(length(rest))
    when [first, ...rest]:
        "late"
    otherwise:
        "empty"
say v
set w to match [9, 9]:
    when [a, b] if a is bigger than b:
        "a wins"
    when [a, b]:
        "b wins or tie"
    otherwise:
        "no"
say w
'''),
    ('ok_error', '''\
set picked to match [ok(7)]:
    when [ok v]:
        v * 2
    otherwise:
        0
say picked
to unwrap_or_zero with r:
    give back match r:
        when ok v:
            v
        when error e:
            0
        otherwise:
            -1
say unwrap_or_zero(ok(5))
say unwrap_or_zero(error("bad"))
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

for name, src in MATCH_EXPR_CASES:
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

print('test_match_expr.py: all assertions passed')
