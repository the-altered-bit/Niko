"""Alpha 9/10: differential tests — native backend output vs VM output.

Skips if no C compiler (cc/gcc/clang) is on PATH.

Note: the VM has a known constant-pool bug (2026-09-21): Python
True == 1 and False == 0, so `constants.index(v)` can deduplicate
`yes` with `1` and `no` with `0`, printing the wrong one when both
appear in a program. The native backend is correct. Test programs avoid
mixing yes/no with 1/0 literals to keep the VM output canonical.
"""
import os, pathlib, shutil, subprocess, sys, tempfile

root = pathlib.Path(__file__).resolve().parent.parent
cc = shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')

def niko2(*args, cwd=None, input_text=None):
    env = dict(os.environ, PYTHONPATH=str(root))
    return subprocess.run([sys.executable, '-m', 'niko2', *args],
                          capture_output=True, text=True,
                          cwd=cwd or root, env=env, input=input_text)

def run_vm(src, cwd=None, input_text=None):
    with tempfile.TemporaryDirectory() as tmp:
        p = pathlib.Path(tmp) / 't.niko'
        p.write_text(src, encoding='utf8')
        r = niko2('run', str(p), cwd=cwd, input_text=input_text)
        assert r.returncode == 0, f'VM failed: {r.stderr[:300]}'
        return r.stdout

def run_native_full(src, cwd=None, input_text=None):
    """Run `niko2 native --run`; return (returncode, stdout, stderr) with
    the 'built ...' banner stripped from stdout."""
    with tempfile.TemporaryDirectory() as tmp:
        p = pathlib.Path(tmp) / 't.niko'
        p.write_text(src, encoding='utf8')
        r = niko2('native', str(p), '--run', cwd=cwd, input_text=input_text)
        lines = r.stdout.splitlines(keepends=True)
        if lines and lines[0].startswith('✓ built'):
            lines = lines[1:]
        return r.returncode, ''.join(lines), r.stderr

def run_native(src, cwd=None, input_text=None):
    rc, out, err = run_native_full(src, cwd=cwd, input_text=input_text)
    assert rc == 0, f'native failed: {(out + err)[:500]}'
    return out

def assert_same(src, **kw):
    n = run_native(src, **kw)
    v = run_vm(src, **kw)
    assert n == v, f'mismatch for:\n{src}\nVM: {v!r}\nnative: {n!r}'

CASES = [
    ('say_text_number',
     'say "hello"\nsay 42\nsay 4.5\nsay 0.30000000000000004\n'),
    ('arithmetic',
     'say 1 + 2 * 3\nsay 10 / 4\nsay 2 ** 8\nsay 7 % 3\nsay -5\nsay 10 - 4\n'),
    ('text_ops',
     'set name to "world"\nsay "hi " + name\nsay length("hello")\n'
     'say upper("abc")\nsay lower("XYZ")\nsay trim("  x  ")\n'
     'say replace("aaa", "a", "b")\nsay split("a,b,c", ",")\n'
     'say join(["a", "b"], "-")\nsay has("hello", "ell")\n'
     'say starts_with("hello", "he")\nsay ends_with("hello", "lo")\n'
     'say count_of("hello", "l")\n'),
    ('comparisons',
     'say 1 is smaller than 2\nsay 2 is at most 2\nsay 3 is bigger than 4\n'
     'say 5 is 5\nsay "a" is not "b"\n'
     'say yes and no\nsay yes or no\nsay not yes\n'),
    ('if_otherwise',
     'set x to 7\nif x is 7:\n    say "seven"\notherwise:\n    say "not seven"\n'
     'if x is 8:\n    say "eight"\n'),
    ('repeat_stop_skip',
     'set total to 0\nrepeat 5 times:\n    set total to total + 1\nsay total\n'
     'set n to 0\nwhile n is smaller than 10:\n    set n to n + 1\n'
     '    if n is 3:\n        skip\n    if n is 8:\n        stop\n    say n\n'),
    ('for_loop',
     'set total to 0\nfor each i in [1, 2, 3, 4, 5]:\n'
     '    set total to total + i\nsay total\n'
     'for each c in "hey":\n    say c\n'),
    ('list_ops',
     'set xs to [3, 1, 2]\nsay sorted(xs)\nsay reversed(xs)\n'
     'say unique([1, 1, 2, 2, 3])\nsay sum(xs)\nsay average(xs)\n'
     'put 4 in xs\nsay xs\nremove 1 from xs\nsay xs\n'
     'say item_of(1, xs)\nsay item_of(-1, xs)\n'
     'set xs[1] to 99\nsay xs\n'),
    ('record_ops',
     'set r to {"a": 1, "b": 2}\nsay r["a"]\nsay r.a\nsay keys(r)\n'
     'set r["b"] to 20\nsay r\nsay {"x": "y"}\n'),
    ('functions',
     'to add with a: number, b: number -> number:\n    give back a + b\n'
     'say add(3, 4)\n'
     'to fact with n:\n    if n <= 1:\n        give back 1\n'
     '    give back n * fact(n - 1)\n'
     'say fact(5)\n'
     'to greet with name:\n    say "hi", name\n'
     'greet("there")\n'),
    ('match_results',
     'set m to 2\nmatch m:\n    when 1:\n        say "one"\n'
     '    when 2, 3:\n        say "two or three"\n'
     '    otherwise:\n        say "other"\n'
     'set res to ok(42)\nmatch res:\n    when ok v:\n        say "got", v\n'
     '    when error e:\n        say "bad", e\n'
     'set bad to error("boom")\n'
     'say is_ok(res)\nsay is_error(bad)\n'
     'say unwrap_or(bad, "fallback")\nsay error_message(bad)\n'
     'set tn to try_number("3.14")\nsay unwrap(tn)\n'),
    ('math_builtins',
     'say pi\nsay abs(-7)\nsay ceil(2.1)\nsay floor(2.9)\n'
     'say round(2.5)\nsay sqrt(16)\nsay max(3, 1, 4)\n'
     'say min("pear", "apple")\nsay numbers 1 to 5\n'
     'say number("42")\nsay text(42)\n'),
    ('nested_text',
     'say ["c", "b", "a"]\nsay {"k": "v"}\n'
     'say [["a", "b"], {"x": 1}]\n'),
    ('augassign_put',
     'set total to 10\nadd 5 to total\nsay total\ntake 3 from total\nsay total\n'
     'set xs to [1]\nput 2 in xs\nsay xs\n'),
]

def test_native_differential():
    if not cc:
        print('SKIP: no C compiler on PATH')
        return
    for name, src in CASES:
        assert_same(src)
        print(f'ok: {name}')

def test_native_closures():
    # Alpha 10: the canonical closure programs, byte-identical to the
    # expected output (the VM asserts the same in test_closures.py).
    if not cc:
        print('SKIP: no C compiler on PATH')
        return
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    sys.path.insert(0, str(root))
    from test_closures import CLOSURE_CASES
    for name, src, want in CLOSURE_CASES:
        rc, out, err = run_native_full(src)
        assert rc == 0, f'native[{name}] failed: {(out + err)[:500]}'
        assert out == want, \
            f'native[{name}] mismatch:\ngot  {out!r}\nwant {want!r}'
        print(f'ok: closure {name}')

def test_native_closure_extras():
    # first-class functions beyond the canonical cases: stored in records
    # (index and attribute call), rebinding a function name, equality.
    if not cc:
        print('SKIP: no C compiler on PATH')
        return
    assert_same(
        'to add with a, b:\n    give back a + b\n'
        'set r to {"f": add}\n'
        'say r["f"](1, 2)\n'
        'say r.f(3, 4)\n'
        'set g to add\n'
        'say g is add\n'
        'set add to 5\n'
        'say add\n')
    assert_same(
        'to mk:\n'
        '    set xs to []\n'
        '    to push with v:\n'
        '        put v in xs\n'
        '    to get:\n'
        '        give back xs\n'
        '    give back [push, get]\n'
        'set p to mk()\n'
        'set q to mk()\n'
        'p[1](10)\n'
        'p[1](20)\n'
        'q[1](99)\n'
        'say p[2]()\n'
        'say q[2]()\n')

def test_native_closure_errors():
    # calling a non-function value: same message as the VM
    if not cc:
        print('SKIP: no C compiler on PATH')
        return
    src = 'set r to ok(5)\nset v to unwrap(r)\nsay v(1)\n'
    rc, out, err = run_native_full(src)
    assert rc != 0, 'calling a non-function should fail'
    assert "I can't call 5 as a function." in out + err, out + err
    print('ok: cannot-call message')
    # arity is checked at call time, same wording as the VM
    src = 'to add with a, b:\n    give back a + b\nsay add(1)\n'
    rc, out, err = run_native_full(src)
    assert rc != 0, 'arity mismatch should fail'
    assert 'add expected 2 arguments, got 1.' in out + err, out + err
    print('ok: arity message')
    # builtins are still not values (checker rejects before codegen)
    src = 'set f to length\n'
    rc, out, err = run_native_full(src)
    assert rc != 0
    assert 'can\'t use the builtin "length" as a value' in out + err, out + err
    print('ok: builtin-as-value rejected')

def test_native_files():
    # file builtins work in native builds (the WASM backend lacks these)
    if not cc:
        print('SKIP: no C compiler on PATH')
        return
    src = ('write_file("t.txt", "a\\nb\\n")\n'
           'say file_exists("t.txt")\n'
           'say read_file("t.txt")\n'
           'say read_lines("t.txt")\n'
           'append_file("t.txt", "c\\n")\n'
           'say read_lines("t.txt")\n'
           'set r to try_read_file("missing.txt")\n'
           'say is_error(r)\n'
           'say error_message(r)\n')
    with tempfile.TemporaryDirectory() as tmp:
        assert_same(src, cwd=tmp)

def test_native_ask():
    # ask reads from stdin in native builds
    if not cc:
        print('SKIP: no C compiler on PATH')
        return
    src = ('ask "name? " into name\n'
           'say "hi", name\n'
           'ask number "num? " into n\n'
           'say n * 2\n')
    assert_same(src, input_text='casper\n21\n')

if __name__ == '__main__':
    test_native_differential()
    test_native_closures()
    test_native_closure_extras()
    test_native_closure_errors()
    test_native_files()
    test_native_ask()
    print('all native tests passed')
