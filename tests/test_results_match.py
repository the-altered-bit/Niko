"""Alpha 6: option<T>/result<T> error model and match/when pattern matching.

Positive coverage lives in tests/niko2_cases/results.niko and
tests/niko2_cases/match.niko. This file asserts the typechecker rejects
misuse with a clear message and a non-zero exit, checks runtime failure
messages, and exercises file-backed results in a hermetic temp dir.
"""
import os, pathlib, subprocess, sys, tempfile

root = pathlib.Path(__file__).resolve().parent.parent

def niko2(*args, cwd=None, input_text=None):
    env = dict(os.environ, PYTHONPATH=str(root))
    return subprocess.run([sys.executable, '-m', 'niko2', *args],
                          capture_output=True, text=True,
                          cwd=cwd or root, env=env, input=input_text)

def check_src(src):
    with tempfile.TemporaryDirectory() as tmp:
        p = pathlib.Path(tmp) / 't.niko'
        p.write_text(src)
        return niko2('check', str(p))

# (source, expected error fragment)
bad = [
    ('set x: option<number> to "a"\n',
     'cannot assign text to option<number> variable "x"'),
    ('set x: option<number> to 1\nset y: text to x\n',
     'cannot assign option<number> to text variable "y"'),
    ('match 5:\n    when ok n:\n        say n\n',
     'cannot match "ok" against number'),
    ('match "a":\n    when error e:\n        say e\n',
     'cannot match "error" against text'),
    ('set r: result<number> to 5\n',
     'cannot assign number to result<number> variable "r"'),
]

# sources that must still pass the checker
good = [
    'set x: option<number> to nothing\n',
    'set x: option<number> to 5\n',
    'set x to ok(1)\nset y: result<number> to x\n',
    'set r to error("x")\nsay unwrap_or(r, 0)\n',
    'match ok(1):\n    when ok n:\n        say n\n    when error e:\n        say e\n',
    'set m: option<text> to nothing\nmatch m:\n    when nothing:\n        say "none"\n    when s:\n        say s\n',
]

for i, (src, frag) in enumerate(bad):
    r = check_src(src)
    assert r.returncode != 0, f'bad[{i}] unexpectedly passed:\n{src}'
    assert frag in (r.stdout + r.stderr), f'bad[{i}] missing {frag!r}:\n{r.stdout}{r.stderr}'

for i, src in enumerate(good):
    r = check_src(src)
    assert r.returncode == 0, f'good[{i}] unexpectedly failed:\n{src}\n{r.stdout}{r.stderr}'

# unwrap on an error re-raises the message itself
r = niko2('run', '/dev/stdin', input_text='say unwrap(error("kaboom"))\n')
assert r.returncode != 0 and 'kaboom' in (r.stdout + r.stderr), r.stdout + r.stderr

# unwrap on nothing is a friendly error
r = niko2('run', '/dev/stdin', input_text='say unwrap(nothing)\n')
assert r.returncode != 0 and 'unwrap nothing' in (r.stdout + r.stderr), r.stdout + r.stderr

# file-backed results, hermetic: write in a temp dir, read back via try_*
with tempfile.TemporaryDirectory() as tmp:
    src = ('write_file("data.txt", "hello")\n'
           'set r to try_read_file("data.txt")\n'
           'match r:\n'
           '    when ok text:\n'
           '        say "got: " + text\n'
           '    when error msg:\n'
           '        say "lost: " + msg\n'
           'set missing to try_read_file("nope.txt")\n'
           'say is_error(missing)\n')
    r = niko2('run', '/dev/stdin', cwd=tmp, input_text=src)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.splitlines() == ['got: hello', 'yes'], repr(r.stdout)

print('test_results_match.py: all assertions passed')
