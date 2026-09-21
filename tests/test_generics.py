"""Alpha 6: list<T> generics are really checked.

Positive coverage lives in tests/niko2_cases/generics.niko. This file
asserts the typechecker rejects element-type violations with a clear
message and a non-zero exit.
"""
import os, pathlib, subprocess, sys, tempfile

root = pathlib.Path(__file__).resolve().parent.parent

def check(path):
    env = dict(os.environ, PYTHONPATH=str(root))
    return subprocess.run([sys.executable, '-m', 'niko2', 'check', str(path)],
                          capture_output=True, text=True, cwd=root, env=env)

# (source, expected error fragment)
bad = [
    ('set xs: list<number> to ["a"]\n',
     'cannot assign text to list<number> variable "xs"'),
    ('set xs: list<number> to [1, "a"]\n',
     'cannot assign text to list<number> variable "xs"'),
    ('set nums to [1, 2]\nput "x" in nums\n',
     'cannot put text in list<number>'),
    ('set nums: list<number> to [1]\nset s: text to nums[0]\n',
     'cannot assign number to text variable "s"'),
    ('set grid: list<list<number>> to [[1], ["a"]]\n',
     'cannot assign list<text> to list<list<number>> variable "grid"'),
    ('set a to [1]\nset b: list<text> to a\n',
     'cannot assign list<number> to list<text> variable "b"'),
]

# sources that must still pass
good = [
    'set xs: list<number> to []\n',
    'set mixed to [1, "a"]\nsay mixed\n',  # heterogeneous lists stay legal
    'set a to [1, 2]\nset b: list<number> to a\n',
]

with tempfile.TemporaryDirectory() as tmp:
    for i, (src, frag) in enumerate(bad):
        p = pathlib.Path(tmp) / f'bad{i}.niko'
        p.write_text(src, encoding='utf8')
        res = check(p)
        assert res.returncode == 1, (src, res.stdout, res.stderr)
        assert frag in (res.stdout + res.stderr), (src, res.stdout, res.stderr)
    for i, src in enumerate(good):
        p = pathlib.Path(tmp) / f'good{i}.niko'
        p.write_text(src, encoding='utf8')
        res = check(p)
        assert res.returncode == 0, (src, res.stdout, res.stderr)
    print('generics OK')
