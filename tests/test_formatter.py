import subprocess, sys, tempfile, pathlib

root = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

def run(*args):
    return subprocess.run([sys.executable, '-m', 'niko2', *args], capture_output=True, text=True, cwd=root)

src = '''set name: text to "Niko"
if name is "Niko":
    say "Hello", name
otherwise:
    say "Nope"
match name:
    when "Niko":
        say "matched"
    when other:
        say other
    otherwise:
        say "fell through"
'''

with tempfile.TemporaryDirectory() as tmp:
    p = pathlib.Path(tmp) / 'sample.niko'
    p.write_text(src, encoding='utf8')
    res = run('format', str(p))
    assert res.returncode == 0, res.stderr or res.stdout
    formatted = res.stdout
    assert 'set name: text to' in formatted
    assert 'if name is' in formatted
    assert 'otherwise:' in formatted
    assert 'match name:' in formatted
    assert 'when other:' in formatted
    from niko2.parser import parse
    parse(formatted)
    print('formatter OK')


# Alpha 23: `use` renders with plain double quotes (used to double them:
# `use "a.niko"` came out as `use '"a.niko"'`).
src_use = 'use "a.niko"\nuse \'b.niko\'\nsay 1\n'
with tempfile.TemporaryDirectory() as tmp:
    p = pathlib.Path(tmp) / 'use.niko'
    p.write_text(src_use, encoding='utf8')
    res = run('format', str(p))
    assert res.returncode == 0, res.stderr or res.stdout
    assert 'use "a.niko"' in res.stdout, res.stdout
    assert 'use "b.niko"' in res.stdout, res.stdout
    assert '\'"a.niko"\'' not in res.stdout, res.stdout
    parse(res.stdout)
    print('formatter use-quotes OK')
