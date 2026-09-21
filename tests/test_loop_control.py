"""Alpha 15: `skip`/`stop` inside `match` arms inside loops (WASM codegen fix).

The WASM backend used to hang node forever when `skip` appeared inside a
`match` arm inside a `for`/`repeat` loop: `br` to the loop's `loop` label
re-ran the bounds check without advancing the index, so the same iteration
repeated forever. The fix advances the index on the `skip` path, exactly
like the VM's continue. Every case below runs 3-way differential
(VM/WASM/native, byte-identical) with timeouts, so a regression can't hang
the suite.
"""
import os, pathlib, shutil, subprocess, sys, tempfile

root = pathlib.Path(__file__).resolve().parent
project_root = root.parent

def niko2(*args, cwd=None, input_text=None, timeout=60):
    env = dict(os.environ, PYTHONPATH=str(project_root))
    return subprocess.run([sys.executable, '-m', 'niko2', *args],
                          capture_output=True, text=True,
                          cwd=cwd or project_root, env=env,
                          input=input_text, timeout=timeout)

LOOP_CONTROL_CASES = [
    ('skip_for', '''\
for each x in [1, 2, 3, 4]:
    skip
    say "never"
say "done"
'''),
    ('skip_for_selective', '''\
for each x in [1, 2, 3]:
    if x is 2:
        skip
    say x
say "done"
'''),
    ('skip_repeat', '''\
set d to 0
repeat 4 times:
    set d to d + 1
    if d is 2:
        skip
    say d
say "done"
'''),
    ('skip_while', '''\
set n to 0
while n < 5:
    set n to n + 1
    if n is 3:
        skip
    say n
say "done"
'''),
    # The reported bug: skip inside a match arm inside a for loop.
    ('skip_match_for', '''\
for each xs in [[1], [], [2, 3], []]:
    match xs:
        when []:
            skip
        otherwise:
            say xs
say "done"
'''),
    ('skip_match_while', '''\
set n to 0
while n < 4:
    set n to n + 1
    match n:
        when 2:
            skip
        otherwise:
            say n
say "done"
'''),
    ('stop_match_for', '''\
for each x in [1, 2, 3, 4]:
    match x:
        when 3:
            stop
        otherwise:
            say x
say "done"
'''),
    ('stop_match_while', '''\
set n to 0
while yes:
    set n to n + 1
    match n:
        when 3:
            stop
        otherwise:
            say n
say "done"
'''),
    ('nested_loops_skip_match', '''\
for each a in [1, 2]:
    for each b in [1, 2, 3]:
        match b:
            when 2:
                skip
            otherwise:
                say a * 10 + b
say "done"
'''),
    ('guard_skip', '''\
for each x in [1, 2, 3, 4]:
    match x:
        when y if y is bigger than 2:
            skip
        otherwise:
            say x
say "done"
'''),
    ('skip_after_match', '''\
for each x in [1, 2, 3]:
    match x:
        when 1:
            say "one"
        otherwise:
            say "other"
    skip
    say "never"
say "done"
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

for name, src in LOOP_CONTROL_CASES:
    r = run_vm(src)
    assert r.returncode == 0, f'case[{name}] VM failed:\n{r.stdout}{r.stderr}'
    vm_out = r.stdout
    if _node:
        try:
            wasm_out = _run_backend('wasm', src)
        except subprocess.TimeoutExpired:
            raise AssertionError(f'3-way[{name}] WASM timed out (loop hang?)')
        assert wasm_out == vm_out, f'3-way[{name}] WASM != VM:\n{wasm_out!r}\n{vm_out!r}'
    else:
        print('SKIP 3-way WASM: node.js not on PATH')
    if _cc:
        try:
            native_out = _run_backend('native', src)
        except subprocess.TimeoutExpired:
            raise AssertionError(f'3-way[{name}] native timed out (loop hang?)')
        assert native_out == vm_out, f'3-way[{name}] native != VM:\n{native_out!r}\n{vm_out!r}'
    else:
        print('SKIP 3-way native: no C compiler on PATH')
print('3-way differential (VM/WASM/native): all matched')

print('test_loop_control.py: all assertions passed')
