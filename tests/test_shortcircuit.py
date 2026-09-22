#!/usr/bin/env python3
"""Alpha 30: short-circuit `and`/`or` with operand-returning semantics.

Niko 1's rule (transpiler passthrough to Python): `and`/`or` return an
OPERAND, not a boolean. Truthiness follows Python: falsy = `no`, `0`,
`0.0`, `""`, `[]`, `{}`, `nothing`; everything else is truthy. The right
side never evaluates when the left side decides.

All happy-path cases run 3-way differential (VM/WASM/native):
byte-identical output. Error cases are VM-only (each backend surfaces
errors differently; the semantics under test is whether the right side
evaluates at all).
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
    # --- truth tables under Niko 1's rule --------------------------------
    ('empty_text_or', {'t.niko': 'say "" or "d"\n'}, 't.niko', 'd\n'),
    ('zero_and', {'t.niko': 'say 0 and 99\n'}, 't.niko', '0\n'),
    ('zero_float_or', {'t.niko': 'say 0.0 or "z"\n'}, 't.niko', 'z\n'),
    ('empty_list_or', {'t.niko': 'say [] or [1]\n'}, 't.niko', '[1]\n'),
    ('empty_record_and', {'t.niko': 'say {} and 1\n'}, 't.niko', '{}\n'),
    ('nothing_or', {'t.niko': 'say nothing or "n"\n'}, 't.niko', 'n\n'),
    ('no_or_text', {'t.niko': 'say no or "x"\n'}, 't.niko', 'x\n'),
    ('yes_and_number', {'t.niko': 'say yes and 42\n'}, 't.niko', '42\n'),
    ('no_and_no', {'t.niko': 'say no and no\n'}, 't.niko', 'no\n'),
    ('yes_or_no', {'t.niko': 'say yes or no\n'}, 't.niko', 'yes\n'),
    # --- constant-pool dedup: True/1 must stay distinct (Alpha 30) -------
    ('dedup_yes_and_1', {'t.niko': 'say yes and 1\n'}, 't.niko', '1\n'),
    ('dedup_no_or_1', {'t.niko': 'say no or 1\n'}, 't.niko', '1\n'),
    ('dedup_1_and_yes', {'t.niko': 'say 1 and yes\n'}, 't.niko', 'yes\n'),
    ('dedup_float_1', {'t.niko': 'say yes and 1.0\n'}, 't.niko', '1\n'),
    ('dedup_0_and_5', {'t.niko': 'say 0.0 and 5\n'}, 't.niko', '0\n'),
    # --- skipped side never evaluates ------------------------------------
    ('skip_or_no_call', {'t.niko': '''\
to marker:
    say "MARKER"
    give back 1
say yes or marker()
say "done"
'''}, 't.niko', 'yes\ndone\n'),
    ('skip_and_no_call', {'t.niko': '''\
to marker:
    say "MARKER"
    give back 1
say no and marker()
say "done"
'''}, 't.niko', 'no\ndone\n'),
    ('skip_or_divzero', {'t.niko': 'say yes or 1/0\nsay "done"\n'},
     't.niko', 'yes\ndone\n'),
    ('skip_and_divzero', {'t.niko': 'say no and 1/0\nsay "done"\n'},
     't.niko', 'no\ndone\n'),
    ('taken_side_evaluates', {'t.niko': '''\
to give1:
    give back 1
say no or give1()
say yes and give1()
'''}, 't.niko', '1\n1\n'),
    # --- chained / mixed precedence --------------------------------------
    ('chain_and', {'t.niko': 'say yes and yes and 7\n'}, 't.niko', '7\n'),
    ('chain_or', {'t.niko': 'say no or no or 7\n'}, 't.niko', '7\n'),
    ('mixed_or_and', {'t.niko': 'say no or yes and 9\n'}, 't.niko', '9\n'),
    ('mixed_and_or', {'t.niko': 'say yes and no or "w"\n'}, 't.niko', 'w\n'),
    # --- contexts ----------------------------------------------------------
    ('ctx_if', {'t.niko': '''\
if "" or "truthy":
    say "in-if"
'''}, 't.niko', 'in-if\n'),
    ('ctx_if_false', {'t.niko': '''\
if "" and "x":
    say "nope"
otherwise:
    say "else"
'''}, 't.niko', 'else\n'),
    ('ctx_while', {'t.niko': '''\
set c to 0
while c < 3 and yes:
    set c to c + 1
say c
'''}, 't.niko', '3\n'),
    ('ctx_while_stop_skip', {'t.niko': '''\
set c to 0
set seen to 0
while no or c < 5:
    set c to c + 1
    if c is 2:
        skip
    if c is 4:
        stop
    set seen to seen + 1
say c
say seen
'''}, 't.niko', '4\n2\n'),
    ('ctx_call_arg', {'t.niko': '''\
to echo1 with x:
    give back x
say echo1(no or 5)
say echo1(yes and 5)
say echo1(yes or 9)
'''}, 't.niko', '5\n5\nyes\n'),
    ('ctx_match_guard', {'t.niko': '''\
set x to 1
match x:
    when 1 if "" or yes:
        say "guard-taken"
    otherwise:
        say "no-match"
'''}, 't.niko', 'guard-taken\n'),
    ('ctx_nested_expr', {'t.niko': '''\
set a to no or 1
set b to yes and 2
say a + b
say text(no or "z")
'''}, 't.niko', '3\nz\n'),
    # --- the SSG guard idiom (Alpha 30): bounds check short-circuits ------
    ('ssg_guard_safe', {'t.niko': '''\
set i to 0
set n to 3
set s to "ab*"
if i < n and item (i + 1) of s is "*":
    say "hit"
say "ok"
'''}, 't.niko', 'ok\n'),
    ('ssg_guard_hit', {'t.niko': '''\
set i to 2
set n to 3
set s to "ab*"
if i < n and item (i + 1) of s is "*":
    say "hit"
say "ok"
'''}, 't.niko', 'hit\nok\n'),
    # --- operand-returning still usable as a boolean ----------------------
    ('bool_ctx_if', {'t.niko': '''\
if yes and no:
    say "nope"
otherwise:
    say "falsey"
'''}, 't.niko', 'falsey\n'),
    ('bool_ctx_not', {'t.niko': 'say not (yes and 7)\n'}, 't.niko', 'no\n'),
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
                f'3-way[{name}] native != VM:\n{native_out!r}\n{native_out!r}'
        else:
            print('SKIP 3-way native: no C compiler on PATH')

# --- error cases (VM only) ------------------------------------------------
with tempfile.TemporaryDirectory() as t:
    tmp = pathlib.Path(t)
    # Error in the TAKEN right operand fires.
    (tmp / 'err_taken.niko').write_text(
        'say "first"\nsay yes and 1/0\nsay "never"\n', encoding='utf8')
    r = _niko2('run', 'err_taken.niko', cwd=tmp)
    assert r.returncode != 0, 'vm[err_taken] should have failed'
    assert 'divide by zero' in r.stdout + r.stderr, \
        f'vm[err_taken] wrong error:\n{r.stdout}{r.stderr}'
    assert 'first' in r.stdout, 'vm[err_taken] printed before failing'

    # Error in the SKIPPED right operand never fires.
    (tmp / 'err_skipped.niko').write_text(
        'say yes or 1/0\nsay no and 1/0\nsay "clean"\n', encoding='utf8')
    r = _niko2('run', 'err_skipped.niko', cwd=tmp)
    assert r.returncode == 0, f'vm[err_skipped] failed:\n{r.stdout}{r.stderr}'
    assert r.stdout == 'yes\nno\nclean\n', \
        f'vm[err_skipped] mismatch:\n{r.stdout!r}'

    # Check-time error in the right operand is reported at its own line.
    (tmp / 'err_check_line.niko').write_text(
        'say yes\nsay yes and undefined_name_xyz\n', encoding='utf8')
    r = _niko2('run', 'err_check_line.niko', cwd=tmp)
    assert r.returncode != 0, 'vm[err_check_line] should have failed'
    assert 'line 2' in r.stdout + r.stderr, \
        f'vm[err_check_line] wrong line:\n{r.stdout}{r.stderr}'
    assert 'undefined_name_xyz' in r.stdout + r.stderr

# --- typechecker (no subprocess) -------------------------------------------
from niko2.parser import parse as parse_src
from niko2 import typecheck

def check_clean(src):
    try:
        typecheck.check(parse_src(src))
    except Exception as e:
        raise AssertionError(f'typechecker[{src!r}] raised: {e}')

def check_err(src, needle):
    try:
        typecheck.check(parse_src(src))
    except Exception as e:
        assert needle in str(e), \
            f'typechecker[{src!r}] error missing {needle!r}: {e}'
    else:
        raise AssertionError(f'typechecker[{src!r}] expected an error')

# Non-boolean operands check cleanly (Alpha 30 rule).
check_clean('say 1 and 2\n')
check_clean('say "a" or "b"\n')
check_clean('say [] or [1]\n')
# Boolean and/or still types as boolean where a boolean is wanted.
check_clean('if yes and no:\n    say 1\n')
check_clean('if yes or no:\n    say 1\n')
# A genuine error in the right operand still fires.
check_err('say yes and undefined_name_xyz\n', 'unknown name')

print('test_shortcircuit.py: all assertions passed')
