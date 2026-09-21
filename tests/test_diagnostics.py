import pathlib, subprocess, sys, tempfile

root = pathlib.Path(__file__).resolve().parent.parent

def run(*args):
    return subprocess.run([sys.executable, '-m', 'niko2', *args], capture_output=True, text=True, cwd=root)

with tempfile.TemporaryDirectory() as tmp:
    def check(src):
        p = pathlib.Path(tmp) / 'bad.niko'
        p.write_text(src, encoding='utf8')
        res = run('run', str(p))
        assert res.returncode == 1, src
        return res.stdout or res.stderr

    # WIP10: unknown identifiers offer a close-match suggestion.
    out = check('say totl(42)\n')
    assert 'unknown name "totl"' in out
    assert 'Did you mean "text"' in out

    # WIP11: errors carry source columns and carets.
    # unknown name: caret spans the mistyped name.
    assert 'line 1, column 5' in out
    assert '  say totl(42)' in out
    assert '      ^^^^' in out

    # parse error: missing colon points just past the end of the line.
    out = check('if x\n    say 1\n')
    assert 'line 1, column 5' in out
    assert 'block statement needs ":"' in out
    assert '  if x\n      ^' in out

    # parse error: a dangling operator is pointed at.
    out = check('say 2 +\n')
    assert 'line 1, column 7' in out
    assert 'unexpected end of expression' in out
    assert '        ^' in out

    # the right line is reported when the error is not on line 1.
    out = check('say 1\nsay 2 +\nsay 3\n')
    assert 'line 2, column 7' in out

    # statement-shape errors point at the offending name.
    out = check('add 1 to 2x\n')
    assert 'line 1, column 10' in out
    assert '"add ... to" needs a plain variable name' in out

    # type errors without a name token still echo the source line.
    out = check('set x: number to "hi"\n')
    assert 'line 1' in out
    assert '  set x: number to "hi"' in out
    assert 'cannot assign text to number variable "x"' in out

    print('diagnostic suggestions OK')
    print('diagnostic columns/carets OK')
