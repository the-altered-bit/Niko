"""Alpha 38: 3-way differential tests for `otherwise if` chains on the native backend.

Root cause (fixed in niko2/backends/native.py `_gen_if_chain`): the old
flat `else if` emission called gen_expr(cond) *before* emitting the
`else if` line, so temp-variable statements landed between the previous
branch's closing `}` and the `else if` -- invalid C ('else' without a
previous 'if'). Worse, the and/or lowering's own `if (...) {...}`
captured the `else if` (dangling else) and silently ran the wrong branch.
The fix nests each subsequent branch inside the previous `else { ... }`.

Every case asserts the VM output first (the VM is the reference), then
requires byte-identical output from WASM (if node is present) and native
(if a C compiler is present). The native leg inherently asserts the
generated C compiles -- a compile failure is a test failure.

Skips the WASM leg if node.js is missing, the native leg if no C compiler
is on PATH.
"""
import os, pathlib, shutil, subprocess, sys, tempfile

root = pathlib.Path(__file__).resolve().parent.parent

# (name, source, expected VM stdout)
CASES = [
    # The loud variant: temp-emitting condition used to break C compilation.
    ('temp_cond_compiles',
     'if 1 is 1:\n'
     '    say 1\n'
     'otherwise if length([1, 2]) is 2:\n'
     '    say 2\n'
     'otherwise:\n'
     '    say 3\n',
     '1\n'),
    # The silent variant: `or` in the condition used to attach the chain's
    # `else if` to the or-lowering's inner `if` and run the wrong branch.
    ('or_cond_right_branch',
     'set c12 to 0\n'
     'while c12 is smaller than 1:\n'
     '    set c12 to c12 + 1\n'
     '    if not (no):\n'
     '        say "first"\n'
     '    otherwise if not (yes):\n'
     '        say "second"\n'
     '    otherwise if [607, 65, c12, c12] or "na\xc3\xafve caf\xc3\xa9":\n'
     '        say nothing, length([c12, c12, 820, -673]) is smaller than number("3.5")\n'
     '    otherwise:\n'
     '        say "fourth"\n'
     '    say c12\n',
     'first\n1\n'),
    # 3+ branches, all temp-emitting conditions, middle branch taken.
    ('chain3_middle',
     'set x to 2\n'
     'if x is 1:\n'
     '    say "one"\n'
     'otherwise if length([1, 2]) is 2:\n'
     '    say "two"\n'
     'otherwise if has({"a": 1}, "a"):\n'
     '    say "three"\n'
     'otherwise:\n'
     '    say "other"\n',
     'two\n'),
    # 3+ branches, all conditions false, otherwise taken.
    ('chain3_otherwise',
     'set x to 9\n'
     'if x is 1:\n'
     '    say "one"\n'
     'otherwise if length([1]) is 2:\n'
     '    say "two"\n'
     'otherwise if has({"a": 1}, "b"):\n'
     '    say "three"\n'
     'otherwise:\n'
     '    say "other"\n',
     'other\n'),
    # `and` in an otherwise-if condition (and/or lowering emits its own if).
    ('and_condition',
     'set x to 5\n'
     'if x is 1:\n'
     '    say "one"\n'
     'otherwise if x is bigger than 3 and length([1, 2, 3]) is 3:\n'
     '    say "big-three"\n'
     'otherwise:\n'
     '    say "other"\n',
     'big-three\n'),
    # Function call in the condition (call temps).
    ('call_condition',
     'to double with n:\n'
     '    give back n * 2\n'
     'set x to 4\n'
     'if x is 1:\n'
     '    say "one"\n'
     'otherwise if double(x) is 8:\n'
     '    say "eight"\n'
     'otherwise:\n'
     '    say "other"\n',
     'eight\n'),
    # Chain nested inside a loop.
    ('nested_in_loop',
     'for each i in [0, 1, 2]:\n'
     '    if i is 0:\n'
     '        say "zero"\n'
     '    otherwise if length([i]) is 1:\n'
     '        say "len-one"\n'
     '    otherwise:\n'
     '        say "other"\n',
     'zero\nlen-one\nlen-one\n'),
    # Chain nested inside a function, called more than once.
    ('nested_in_function',
     'to classify with n:\n'
     '    if n is 0:\n'
     '        give back "zero"\n'
     '    otherwise if length([n, n]) is 2:\n'
     '        give back "pair"\n'
     '    otherwise:\n'
     '        give back "other"\n'
     'say classify(0)\n'
     'say classify(7)\n',
     'zero\npair\n'),
    # Laziness: later conditions must not evaluate once a branch is taken.
    # item_of(99, "ab") would fail loudly if it ran.
    ('short_circuit',
     'if yes:\n'
     '    say "taken"\n'
     'otherwise if item_of(99, "ab") is "x":\n'
     '    say "bad"\n'
     'otherwise:\n'
     '    say "other"\n',
     'taken\n'),
    # Plain if/otherwise with a temp condition (regression guard).
    ('plain_if_otherwise',
     'set x to [1]\n'
     'if length(x) is 5:\n'
     '    say "five"\n'
     'otherwise:\n'
     '    say "not-five"\n',
     'not-five\n'),
    # Record literal in the condition (record-construction temps).
    ('record_has',
     'set r to {"k": "v"}\n'
     'if has(r, "zzz"):\n'
     '    say "zzz"\n'
     'otherwise if has({"a": 1, "b": 2}, "b"):\n'
     '    say "has-b"\n'
     'otherwise:\n'
     '    say "other"\n',
     'has-b\n'),
    # A chain inside a chain body.
    ('chain_in_chain',
     'set x to 2\n'
     'set y to 20\n'
     'if x is 1:\n'
     '    say "x-one"\n'
     'otherwise if length([x]) is 1:\n'
     '    if y is 10:\n'
     '        say "y-ten"\n'
     '    otherwise if length([y, y]) is 2:\n'
     '        say "y-pair"\n'
     '    otherwise:\n'
     '        say "y-other"\n'
     'otherwise:\n'
     '    say "x-other"\n',
     'y-pair\n'),
    # First branch taken in a 4-chain: later temp conditions never emitted
    # at runtime (also exercises deeper nesting depth).
    ('first_of_four',
     'set x to 1\n'
     'if length([x]) is 1:\n'
     '    say "a"\n'
     'otherwise if length([x, x]) is 2:\n'
     '    say "b"\n'
     'otherwise if length([x, x, x]) is 3:\n'
     '    say "c"\n'
     'otherwise if length([x, x, x, x]) is 4:\n'
     '    say "d"\n'
     'otherwise:\n'
     '    say "e"\n',
     'a\n'),
]

node = shutil.which('node')
cc = shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')


def _niko2(*args, cwd):
    env = dict(os.environ, PYTHONPATH=str(root))
    return subprocess.run([sys.executable, '-m', 'niko2', *args],
                          capture_output=True, text=True, cwd=cwd, env=env)


def _strip_banner(out):
    lines = out.splitlines(keepends=True)
    if lines and lines[0].startswith('\u2713 built'):
        lines = lines[1:]
    return ''.join(lines)


for name, src, want in CASES:
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        (tmp / 't.niko').write_text(src, encoding='utf8')
        r = _niko2('run', 't.niko', cwd=tmp)
        assert r.returncode == 0, f'vm[{name}] failed:\n{r.stdout}{r.stderr}'
        assert r.stdout == want, \
            f'vm[{name}] mismatch:\n{r.stdout!r} != {want!r}'
        vm_out = r.stdout
        if node:
            r = _niko2('wasm', 't.niko', '--run', cwd=tmp)
            assert r.returncode == 0, \
                f'wasm[{name}] failed:\n{r.stdout}{r.stderr}'
            wasm_out = _strip_banner(r.stdout)
            assert wasm_out == vm_out, \
                f'3-way[{name}] WASM != VM:\n{wasm_out!r}\n{vm_out!r}'
        else:
            print('SKIP 3-way WASM: node.js not on PATH')
        if cc:
            # --run asserts the C compiles (a compile failure is rc != 0).
            r = _niko2('native', 't.niko', '--run', cwd=tmp)
            assert r.returncode == 0, \
                f'native[{name}] failed (compile or run):\n{r.stdout}{r.stderr}'
            native_out = _strip_banner(r.stdout)
            assert native_out == vm_out, \
                f'3-way[{name}] native != VM:\n{native_out!r}\n{vm_out!r}'
        else:
            print('SKIP 3-way native: no C compiler on PATH')
    print(f'ok: {name}')
