"""Alpha 27 (part 1): the `assert` statement.

Syntax: `assert EXPR` and `assert EXPR, "message"` (message split on the
top-level comma). The condition must be boolean and the message, if
present, must be text (checker errors otherwise). A failed assert raises
the VM's NikoRuntimeError carrying the assert statement's line number;
the message names the expression's source text and includes the user's
message when given.

Backend split: VM and WASM implement `assert` (byte-identical messages);
the native backend raises a clean CompileError naming `assert` as
unsupported, since its C runtime has no text-concat helper to build the
two-arg panic message.
"""
import os, pathlib, shutil, subprocess, sys, tempfile

root = pathlib.Path(__file__).resolve().parent
project_root = root.parent

def niko2(*args, input_text=None, timeout=60):
    env = dict(os.environ, PYTHONPATH=str(project_root))
    return subprocess.run([sys.executable, '-m', 'niko2', *args],
                          capture_output=True, text=True,
                          cwd=project_root, env=env,
                          input=input_text, timeout=timeout)

def run_vm(src):
    return niko2('run', '/dev/stdin', input_text=src)

def check(name, cond):
    assert cond, f'test_assert[{name}] FAILED'
    print(f'ok {name}')

# 1. assert passes: execution continues.
r = run_vm('set x to 5\nassert x is bigger than 3\nsay "passed"\n')
check('passes', r.returncode == 0 and r.stdout == 'passed\n')

# 2. assert fails: NikoRuntimeError with line number + expression source text.
r = run_vm('set x to 5\nassert x is smaller than 3\nsay "never"\n')
check('fails_rc', r.returncode != 0)
check('fails_line', 'Line 2' in r.stdout)
check('fails_expr_named', 'Assertion failed: "x is smaller than 3" is not true.' in r.stdout)
check('fails_halts', 'never' not in r.stdout)

# 3. assert with a custom message: user message is included.
r = run_vm('set x to 5\nassert x is smaller than 3, "x should be small"\n')
check('custom_message',
     r.returncode != 0
     and 'Assertion failed: "x is smaller than 3" is not true: x should be small' in r.stdout
     and 'Line 2' in r.stdout)

# 4. non-boolean condition is a checker type error with a line number.
r = run_vm('assert 5\n')
check('nonboolean_type_error',
     r.returncode != 0
     and 'assert condition must be boolean, got number' in r.stdout
     and 'line 1' in r.stdout)

# 5. non-text message is a checker type error with a line number.
r = run_vm('assert yes, 42\n')
check('nontext_message_type_error',
     r.returncode != 0
     and 'assert message must be text, got number' in r.stdout
     and 'line 1' in r.stdout)

# 6. assert inside a function body (pass and fail).
r = run_vm('to check with n:\n'
           '    assert n is bigger than 0, "n must be positive"\n'
           '    say "ok"\n'
           'check(3)\n'
           'check(0)\n')
check('function_body',
     r.returncode != 0
     and r.stdout.startswith('ok\n')
     and 'Line 2' in r.stdout
     and 'Assertion failed: "n is bigger than 0" is not true: n must be positive' in r.stdout)

# 7. identifiers starting with `assert` still parse as expressions.
r = run_vm('set assert_foo to 42\nsay assert_foo\n')
check('assert_prefix_identifier', r.returncode == 0 and r.stdout == '42\n')

# 8. the message is lazy: not evaluated when the condition holds.
r = run_vm('set log to []\n'
           'to note with t:\n'
           '    put t in log\n'
           '    give back t\n'
           'assert yes, note("hi")\n'
           'say log\n')
check('message_lazy', r.returncode == 0 and r.stdout == '[]\n')

# 9. malformed assert forms are parse errors, not silent miscompiles.
for bad in ('assert\n',
            'assert x,\n',
            'assert x, "a", "b"\n'):
    r = run_vm(bad)
    check(f'parse_error:{bad.strip()!r}', r.returncode != 0 and 'Niko error' in r.stdout)

# 10. formatter round-trips assert (both forms).
r = niko2('format', '/dev/stdin',
          input_text='set x to 1\nassert x == 1\nassert x == 1, "one"\n')
check('format', r.returncode == 0
     and r.stdout == 'set x to 1\nassert x == 1\nassert x == 1, \'one\'\n')

# 11. WASM differential: passing programs print the same; failing programs
#     panic with the same message body (wasm host shim reports on stderr).
_node = shutil.which('node')
def run_wasm(src):
    with tempfile.TemporaryDirectory() as tmp:
        p = pathlib.Path(tmp) / 't.niko'
        p.write_text(src, encoding='utf8')
        r = niko2('wasm', str(p), '--run')
        lines = r.stdout.splitlines(keepends=True)
        if lines and lines[0].startswith('✓ built'):
            lines = lines[1:]
        return r.returncode, ''.join(lines), r.stderr

if _node:
    PASS_CASES = [
        'set x to 5\nassert x is bigger than 3\nsay "passed"\n',
        'set x to 5\nassert x is bigger than 3, "fine"\nsay "passed"\n',
        'to check with n:\n    assert n is bigger than 0\n    say "ok"\ncheck(1)\n',
    ]
    for i, src in enumerate(PASS_CASES):
        v = run_vm(src)
        wrc, wout, werr = run_wasm(src)
        check(f'wasm_pass_{i}',
             v.returncode == 0 and wrc == 0 and wout == v.stdout)
    FAIL_CASES = [
        ('set x to 5\nassert x is smaller than 3\n',
         'Line 2: Assertion failed: "x is smaller than 3" is not true.'),
        ('set x to 5\nassert x is smaller than 3, "x should be small"\n',
         'Line 2: Assertion failed: "x is smaller than 3" is not true: x should be small'),
    ]
    for i, (src, body) in enumerate(FAIL_CASES):
        v = run_vm(src)
        wrc, wout, werr = run_wasm(src)
        check(f'wasm_fail_{i}',
             v.returncode != 0 and wrc != 0
             and body in v.stdout and body in werr)
else:
    print('SKIP WASM differential: node.js not on PATH')

# 12. native backend: clean CompileError naming assert as unsupported.
with tempfile.TemporaryDirectory() as tmp:
    p = pathlib.Path(tmp) / 't.niko'
    p.write_text('assert yes\n', encoding='utf8')
    r = niko2('native', str(p))
    check('native_unsupported',
         r.returncode != 0 and 'assert is not supported on the native backend' in r.stdout)

print('test_assert.py: all assertions passed')
