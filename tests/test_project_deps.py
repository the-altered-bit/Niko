import json, pathlib, subprocess, sys, tempfile

root = pathlib.Path(__file__).resolve().parent.parent


def run(*args, cwd=None):
    return subprocess.run([sys.executable, '-m', 'niko2', *args], capture_output=True, text=True, cwd=cwd or root)

with tempfile.TemporaryDirectory() as tmp:
    proj = pathlib.Path(tmp) / 'demo'
    proj.mkdir()
    (proj / 'niko.toml').write_text('name = "demo"\nversion = "0.1.0"\n\n[dependencies]\n', encoding='utf8')
    (proj / 'main.niko').write_text('use "helpers.niko"\nset answer: number to twice(21)\nsay answer\n', encoding='utf8')
    (proj / 'helpers.niko').write_text('to twice with x: number -> number:\n    give back x * 2\n', encoding='utf8')
    res = run('deps', str(proj))
    assert res.returncode == 0, res.stderr or res.stdout
    assert 'main.niko: helpers.niko' in res.stdout
    lock = run('lock', str(proj))
    assert lock.returncode == 0, lock.stderr or lock.stdout
    lock_data = json.loads((proj / 'niko.lock').read_text(encoding='utf8'))
    assert 'main.niko' in lock_data['dependencies']
    assert 'helpers.niko' in lock_data['dependencies']['main.niko']
    print('dependency graph OK')
