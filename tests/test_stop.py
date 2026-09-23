#!/usr/bin/env python3
"""Alpha 37: `stop` out of `repeat`/`for each` must pop the VM iterator.

The VM's `stop` used to jump straight to the loop end without popping the
loop's iterator off the frame's `iter_stack`. The stale iterator then
corrupted the enclosing loop's `ITER_NEXT`: hangs or wrong iteration
values. The compiler now emits `ITER_POP` before the break jump when
`stop` targets a `repeat`/`for each` loop. `skip` (which jumps to the loop
head where the live iterator is advanced) and `stop` out of `while` (no
iterator) are unchanged.

Every happy-path case runs 3-way differential (VM/WASM/native):
byte-identical output, with the expected stdout pinned against the VM.
Timeouts guard each backend so a regression hangs the case, not the suite.
The `stop`-in-a-function compile error is VM-only (each backend surfaces
compile errors differently).
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

# (name, source, expected stdout)
CASES = [
    # --- baselines: stop/skip with no nesting --------------------------------
    ('stop_repeat', '''\
set c to 0
repeat 5 times:
    set c to c + 1
    if c is 3:
        stop
    say c
say "after"
''', '1\n2\nafter\n'),
    ('stop_for', '''\
for each x in [1, 2, 3, 4]:
    if x is 3:
        stop
    say x
say "after"
''', '1\n2\nafter\n'),
    ('stop_while', '''\
set n to 0
while n < 5:
    set n to n + 1
    if n is 3:
        stop
    say n
say "after"
''', '1\n2\nafter\n'),
    ('skip_repeat', '''\
set c to 0
repeat 4 times:
    set c to c + 1
    if c is 2:
        skip
    say c
say "after"
''', '1\n3\n4\nafter\n'),
    ('skip_for', '''\
for each x in [1, 2, 3, 4]:
    if x is 2:
        skip
    say x
say "after"
''', '1\n3\n4\nafter\n'),
    ('skip_while', '''\
set n to 0
while n < 5:
    set n to n + 1
    if n is 2:
        skip
    say n
say "after"
''', '1\n3\n4\n5\nafter\n'),
    # --- nesting: stop in the inner loop targets the inner loop -------------
    # The outer loop's values are observable, so a leaked inner iterator
    # (the pre-fix bug) would show up as wrong output, not just a hang.
    ('stop_repeat_in_for', '''\
for each a in [1, 2, 3]:
    set seen to 0
    repeat 5 times:
        set seen to seen + 1
        if seen is 2:
            stop
    say a
say "after"
''', '1\n2\n3\nafter\n'),
    ('stop_for_in_repeat', '''\
set log to []
repeat 3 times:
    for each b in [10, 20, 30]:
        if b is 20:
            stop
        put b in log
say log
say "after"
''', '[10, 10, 10]\nafter\n'),
    ('stop_for_in_for', '''\
set out to []
for each a in [1, 2, 3]:
    for each b in [10, 20]:
        if b is 20:
            stop
        put a * 10 + b in out
say out
say "after"
''', '[20, 30, 40]\nafter\n'),
    ('stop_repeat_in_repeat', '''\
set n to 0
repeat 3 times:
    set m to 0
    repeat 4 times:
        set m to m + 1
        if m is 2:
            stop
    set n to n + m
say n
say "after"
''', '6\nafter\n'),
    ('stop_while_in_for', '''\
for each a in [1, 2, 3]:
    set w to 0
    while w < 5:
        set w to w + 1
        if w is 2:
            stop
    say a * 100 + w
say "after"
''', '102\n202\n302\nafter\n'),
    ('stop_for_in_while', '''\
set n to 0
set out to []
while n < 3:
    set n to n + 1
    for each b in [7, 8, 9]:
        if b is 8:
            stop
        put b in out
say out
say "after"
''', '[7, 7, 7]\nafter\n'),
    ('stop_repeat_in_while', '''\
set n to 0
set hits to 0
while n < 3:
    set n to n + 1
    repeat 5 times:
        set hits to hits + 1
        stop
say hits
say "after"
''', '3\nafter\n'),
    ('stop_while_in_repeat', '''\
set out to []
repeat 3 times:
    set w to 0
    while w < 10:
        set w to w + 1
        if w is 3:
            stop
    put w in out
say out
say "after"
''', '[3, 3, 3]\nafter\n'),
    # --- skip in nested loops: unchanged behavior ------------------------------
    ('skip_for_in_for', '''\
set out to []
for each a in [1, 2]:
    for each b in [1, 2, 3]:
        if b is 2:
            skip
        put a * 10 + b in out
say out
say "after"
''', '[11, 13, 21, 23]\nafter\n'),
    ('skip_repeat_in_repeat', '''\
set out to []
repeat 2 times:
    set k to 0
    repeat 3 times:
        set k to k + 1
        if k is 2:
            skip
        put k in out
say out
say "after"
''', '[1, 3, 1, 3]\nafter\n'),
    # --- stop reached through a conditional ------------------------------------
    ('stop_if_in_repeat', '''\
set c to 0
repeat 6 times:
    set c to c + 1
    if c is 4:
        say "stopping"
        stop
    say c
say "after"
''', '1\n2\n3\nstopping\nafter\n'),
    ('stop_if_in_for', '''\
for each x in [5, 6, 7, 8]:
    if x is bigger than 6:
        stop
    say x
say "after"
''', '5\n6\nafter\n'),
    # --- stop reached through a match ------------------------------------------
    ('stop_match_in_for', '''\
for each x in [1, 2, 3, 4]:
    match x:
        when 3:
            stop
        otherwise:
            say x
say "after"
''', '1\n2\nafter\n'),
    ('stop_match_guard_in_for', '''\
for each x in [1, 2, 3, 4]:
    match x:
        when y if y is bigger than 2:
            stop
        otherwise:
            say x
say "after"
''', '1\n2\nafter\n'),
    # --- triple nesting ---------------------------------------------------------
    ('triple_for_repeat_for', '''\
set out to []
for each a in [1, 2]:
    repeat 3 times:
        for each c in [7, 8, 9]:
            if c is 8:
                stop
            put a * 100 + c in out
say out
say "after"
''', '[107, 107, 107, 207, 207, 207]\nafter\n'),
    ('triple_repeat_for_while', '''\
set out to []
repeat 2 times:
    for each b in [1, 2]:
        set w to 0
        while w < 9:
            set w to w + 1
            if w is 2:
                stop
        put b * 10 + w in out
say out
say "after"
''', '[12, 22, 12, 22]\nafter\n'),
    # --- the inner stop must not disturb the outer loop's iteration -------------
    ('outer_visits_all_for', '''\
set seen to []
for each x in [1, 2, 3, 4, 5]:
    for each y in [9, 8]:
        stop
    put x in seen
say seen
say "after"
''', '[1, 2, 3, 4, 5]\nafter\n'),
    ('outer_count_repeat', '''\
set n to 0
repeat 4 times:
    set n to n + 1
    for each y in [1, 2, 3]:
        if y is 1:
            stop
    say n
say "after"
''', '1\n2\n3\n4\nafter\n'),
    # --- stop as first / last statement of the body -----------------------------
    ('stop_first_for', '''\
for each x in [1, 2, 3]:
    stop
    say "unreached"
say "after"
''', 'after\n'),
    ('stop_last_for', '''\
for each x in [1, 2, 3]:
    say x
    stop
say "after"
''', '1\nafter\n'),
    ('stop_first_repeat', '''\
set c to 0
repeat 5 times:
    stop
    set c to c + 1
say c
say "after"
''', '0\nafter\n'),
    ('stop_last_repeat', '''\
set c to 0
repeat 5 times:
    set c to c + 1
    stop
say c
say "after"
''', '1\nafter\n'),
    # --- loop variables stay bound after a stopped inner loop --------------------
    ('inner_var_bound', '''\
set out to []
for each x in [10, 20]:
    set last_y to 0
    for each y in [1, 2, 3]:
        set last_y to y
        if y is 2:
            stop
    put x + last_y in out
say out
say "after"
''', '[12, 22]\nafter\n'),
    # --- empty-iteration edges -----------------------------------------------------
    ('repeat_zero_with_stop', '''\
repeat 0 times:
    stop
    say "unreached"
say "zero-done"
''', 'zero-done\n'),
    ('for_empty_with_stop', '''\
for each x in []:
    stop
    say "unreached"
say "empty-done"
''', 'empty-done\n'),
]

def run_vm(src):
    return niko2('run', '/dev/stdin', input_text=src)

def _strip_banner(out):
    lines = out.splitlines(keepends=True)
    if lines and lines[0].startswith('\u2713 built'):
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

for name, src, want in CASES:
    r = run_vm(src)
    assert r.returncode == 0, f'case[{name}] VM failed:\n{r.stdout}{r.stderr}'
    assert r.stdout == want, \
        f'case[{name}] VM mismatch:\n{r.stdout!r} != {want!r}'
    vm_out = r.stdout
    if _node:
        try:
            wasm_out = _run_backend('wasm', src)
        except subprocess.TimeoutExpired:
            raise AssertionError(f'3-way[{name}] WASM timed out (loop hang?)')
        assert wasm_out == vm_out, \
            f'3-way[{name}] WASM != VM:\n{wasm_out!r}\n{vm_out!r}'
    else:
        print('SKIP 3-way WASM: node.js not on PATH')
    if _cc:
        try:
            native_out = _run_backend('native', src)
        except subprocess.TimeoutExpired:
            raise AssertionError(f'3-way[{name}] native timed out (loop hang?)')
        assert native_out == vm_out, \
            f'3-way[{name}] native != VM:\n{native_out!r}\n{vm_out!r}'
    else:
        print('SKIP 3-way native: no C compiler on PATH')
print(f'3-way differential (VM/WASM/native): {len(CASES)} cases matched')

# --- compile errors (VM only): stop/skip inside a function is not inside a loop -
# The compiler resets its loop stack per function, so `stop` in a function
# called from a loop is a compile error, not a break of the caller's loop.
for name, src, stmt in (
    ('stop_in_function', 'to f:\n    stop\nrepeat 3 times:\n    f()\n', 'stop'),
    ('skip_in_function', 'to f:\n    skip\nrepeat 3 times:\n    f()\n', 'skip'),
):
    r = run_vm(src)
    assert r.returncode != 0, f'error[{name}] unexpectedly succeeded'
    assert f'{stmt} must be inside a loop' in r.stdout + r.stderr, \
        f'error[{name}] wrong message:\n{r.stdout}{r.stderr}'

print(f'test_stop.py: all assertions passed ({len(CASES)} differential cases + 2 compile-error cases)')
