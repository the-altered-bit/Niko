#!/usr/bin/env python3
"""Regression tests for the Niko 2 pipeline (parser -> checker -> compiler -> VM).

This is separate from tests/run_tests.py, which only exercises the Niko 1
engine (niko.py). Until this file existed, Niko 2 had no automated
regression coverage of its own -- release notes describing a "regression
suite" for a Niko 2 alpha were, in fact, describing Niko 1 test runs only.

    python tests/run_niko2_tests.py

Each tests/niko2_cases/NAME.niko is run with `niko2 run` and compared
against NAME.out. This intentionally only covers the subset of syntax
Niko 2 currently supports -- see niko2/KNOWN_LIMITATIONS.md for what is
still missing relative to Niko 1.

In addition, one case is round-tripped through `niko2 build` (source ->
.nikoir) and then run again from the compiled .nikoir artifact alone, to
confirm the serialized-IR loader (Alpha 5) reproduces identical output
without the original source.
"""
import pathlib, subprocess, sys, tempfile

root = pathlib.Path(__file__).resolve().parent
project_root = root.parent
cases_dir = root / 'niko2_cases'
failed = 0

def run_niko2(*args, input_text=None):
    return subprocess.run(
        [sys.executable, '-m', 'niko2', *args],
        input=input_text, capture_output=True, text=True, cwd=project_root,
    )

cases = sorted(cases_dir.glob('*.niko'))
for case in cases:
    inp = case.with_suffix('.in')
    input_text = inp.read_text() if inp.exists() else ''
    want = case.with_suffix('.out').read_text()
    got = run_niko2('run', str(case), input_text=input_text).stdout
    if got != want:
        failed += 1
        print(f'FAIL {case.name}\n--- expected ---\n{want}--- got ---\n{got}')
print(f'{len(cases)} niko2 cases, {failed} failures')

# .nikoir round trip: build one case, then run *only* the artifact (the
# .niko source is not consulted) and check the output still matches.
roundtrip_case = cases_dir / 'basics.niko'
if roundtrip_case.exists():
    want = roundtrip_case.with_suffix('.out').read_text()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_niko = pathlib.Path(tmp) / 'basics.niko'
        tmp_niko.write_text(roundtrip_case.read_text())
        build_result = run_niko2('build', str(tmp_niko))
        if build_result.returncode != 0:
            failed += 1
            print(f'FAIL nikoir round trip: build failed\n{build_result.stdout}{build_result.stderr}')
        else:
            nikoir_path = tmp_niko.with_suffix('.nikoir')
            tmp_niko.unlink()  # prove the .niko source is not needed to run the artifact
            got = run_niko2('run', str(nikoir_path)).stdout
            if got != want:
                failed += 1
                print(f'FAIL nikoir round trip\n--- expected ---\n{want}--- got ---\n{got}')
            else:
                print('nikoir round trip: OK (build -> load -> run matched source output)')

print(f'TOTAL niko2 failures: {failed}')
sys.exit(1 if failed else 0)
