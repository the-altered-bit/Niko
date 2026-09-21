"""File helpers and pick() for the Niko 2 standard library.

File tests run in a TemporaryDirectory (as the process working directory)
so they never touch the repo. The deterministic text helpers live in
tests/niko2_cases/stdlib_text.niko instead.
"""
import os
import pathlib, subprocess, sys, tempfile

root = pathlib.Path(__file__).resolve().parent.parent

def run(*args, cwd):
    env = dict(os.environ, PYTHONPATH=str(root))
    return subprocess.run([sys.executable, '-m', 'niko2', *args],
                          capture_output=True, text=True, cwd=cwd, env=env)

with tempfile.TemporaryDirectory() as tmp:
    tmp = pathlib.Path(tmp)
    prog = tmp / 'files.niko'

    # write -> append -> read round trip, all relative to cwd.
    prog.write_text(
        'write_file("scratch.txt", "line1\\n")\n'
        'append_file("scratch.txt", "line2\\n")\n'
        'say read_file("scratch.txt")\n'
        'say read_lines("scratch.txt")\n'
        'say file_exists("scratch.txt")\n'
        'say file_exists("missing.txt")\n',
        encoding='utf8')
    res = run('run', str(prog), cwd=tmp)
    assert res.returncode == 0, res.stderr
    assert 'line1\nline2\n' in res.stdout, res.stdout
    assert '["line1", "line2"]' in res.stdout, res.stdout
    assert res.stdout.rstrip().endswith('yes\nno'), res.stdout
    assert (tmp / 'scratch.txt').read_text(encoding='utf8') == 'line1\nline2\n'

    # missing file: friendly error, non-zero exit.
    prog.write_text('say read_file("missing.txt")\n', encoding='utf8')
    res = run('run', str(prog), cwd=tmp)
    assert res.returncode == 1, res.stdout
    assert 'I couldn\'t find the file "missing.txt"' in (res.stdout + res.stderr)

    # pick returns a member of the collection...
    prog.write_text('say pick([10, 20, 30])\n', encoding='utf8')
    res = run('run', str(prog), cwd=tmp)
    assert res.returncode == 0 and res.stdout.strip() in ('10', '20', '30'), res.stdout

    # ...and refuses an empty one with a friendly error.
    prog.write_text('say pick([])\n', encoding='utf8')
    res = run('run', str(prog), cwd=tmp)
    assert res.returncode == 1, res.stdout
    assert "I can't pick from an empty list" in (res.stdout + res.stderr)

    print('stdlib helpers OK')
