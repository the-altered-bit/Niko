#!/usr/bin/env python3
"""Run every tests/cases/*.niko and compare with its .out file.

    python tests/run_tests.py          # desktop engine (niko.py)
    python tests/run_tests.py --ide    # also check the browser IDE engine (needs node)

A case may have NAME.in (typed answers, one per line) and must have NAME.out
(the expected output). Both engines must print the same thing.
"""
import pathlib, subprocess, sys, tempfile

root = pathlib.Path(__file__).resolve().parent
niko = root.parent / 'niko.py'
check_ide = '--ide' in sys.argv
failed = 0
cases = sorted((root / 'cases').glob('*.niko'))
for case in cases:
    inp = case.with_suffix('.in')
    text = inp.read_text() if inp.exists() else ''
    want = case.with_suffix('.out').read_text()
    work = tempfile.mkdtemp()
    got = subprocess.run([sys.executable, str(niko), str(case)], input=text, capture_output=True, text=True, cwd=work).stdout
    results = [('python', got)]
    if check_ide:
        lines = text.split('\n')[:-1] if text else []
        ide = subprocess.run(['node', str(root / 'ide_harness.js'), str(case), *lines], capture_output=True, text=True).stdout
        results.append(('ide', ide))
    for name, out in results:
        if out != want:
            failed += 1
            print(f'FAIL {case.name} [{name}]\n--- expected ---\n{want}--- got ---\n{out}')
print(f'{len(cases)} cases, {failed} failures')
sys.exit(1 if failed else 0)
