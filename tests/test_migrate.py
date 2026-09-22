"""Tests for `niko2 migrate` (Alpha 35): the Niko 1 -> Niko 2 migration advisor.

Fixture layout (tests/migrate_cases/):
  <name>.niko                Niko 1-style source to analyze
  <name>.niko.expected.json  pinned [[line, severity, code], ...] findings
  <name>.fixed.niko          expected output of applying --fix (only for
                            fixtures where every error is auto-fixable, or
                            where the fixable subset has a pinned result)
"""

import json
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES = os.path.join(REPO, "tests", "migrate_cases")

sys.path.insert(0, REPO)
from niko2.migrate import analyze_source, apply_fixes  # noqa: E402


def _cases():
    out = []
    for f in sorted(os.listdir(CASES)):
        if f.endswith(".niko") and not f.endswith(".fixed.niko"):
            out.append(f)
    return out


def _read(name):
    with open(os.path.join(CASES, name)) as fh:
        return fh.read()


def test_pinned_findings():
    """Every fixture pins exact (line, severity, code) findings."""
    for name in _cases():
        src = _read(name)
        expected = json.load(
            open(os.path.join(CASES, name + ".expected.json"))
        )
        got = [[x.line, x.severity, x.code] for x in analyze_source(src)]
        assert got == expected, f"{name}: {got} != {expected}"


def test_fix_round_trip():
    """--fix output matches the pinned .fixed.niko; re-analysis is clean
    of every applied code; fixing twice is a no-op."""
    for name in _cases():
        fixed_name = name.replace(".niko", ".fixed.niko")
        if not os.path.exists(os.path.join(CASES, fixed_name)):
            continue
        src = _read(name)
        findings = analyze_source(src)
        new_src, applied = apply_fixes(src, findings)
        expected_fixed = _read(fixed_name)
        assert new_src == expected_fixed, (
            f"{name}: --fix output differs from {fixed_name}\n"
            f"--- got ---\n{new_src}\n--- want ---\n{expected_fixed}"
        )
        # Re-analysis: no finding with an applied code may remain.
        remaining = analyze_source(new_src)
        assert all(x.code not in applied for x in remaining), (
            f"{fixed_name}: applied fixes still reported: {remaining}"
        )
        # Idempotency: a second fix pass changes nothing.
        again, _ = apply_fixes(new_src, remaining)
        assert again == new_src, f"{fixed_name}: fix is not idempotent"


def test_fix_needs_no_double_pass():
    """apply_fixes runs its own fixpoint loop: one call is enough."""
    src = _read("not_parens.niko")
    new_src, applied = apply_fixes(src, analyze_source(src))
    assert "not (x is in [1, 2])" in new_src
    assert new_src.count("(") == new_src.count(")")


def _run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "niko2", *args],
        cwd=REPO,
        capture_output=True,
        text=True,
    )


def test_cli_exit_codes():
    """Exit 1 when errors remain, 0 when clean, 2 on missing file."""
    r = _run_cli("migrate", os.path.join(CASES, "calls.niko"))
    assert r.returncode == 1, r.stdout
    assert "[ask-call]" in r.stdout

    r = _run_cli("migrate", os.path.join(CASES, "clean.niko"))
    assert r.returncode == 0, r.stdout
    assert "no migration issues found" in r.stdout

    r = _run_cli("migrate", os.path.join(CASES, "behavior.niko"))
    assert r.returncode == 0, r.stdout  # warnings only -> 0
    assert "[text-render]" in r.stdout

    r = _run_cli("migrate", os.path.join(CASES, "nope.niko"))
    assert r.returncode == 2


def test_cli_fix_rewrites_file(tmp_path):
    """`migrate --fix <file>` rewrites the file in place and re-reports."""
    target = tmp_path / "calls.niko"
    target.write_text(_read("calls.niko"))
    r = _run_cli("migrate", "--fix", str(target))
    assert r.returncode == 0, r.stdout + r.stderr
    assert target.read_text() == _read("calls.fixed.niko")
    assert "applied automatic fixes" in r.stdout
    assert "[ask-call]" not in r.stdout  # fixed findings are gone


def test_niko2_corpus_has_no_errors():
    """Valid Niko 2 code (its own test cases + stdlib) must not produce
    *error* findings -- the checker's clean verdict is ground truth."""
    niko2_cases = os.path.join(REPO, "tests", "niko2_cases")
    stdlib = os.path.join(REPO, "niko2", "stdlib")
    checked = 0
    for d in (niko2_cases, stdlib):
        for f in sorted(os.listdir(d)):
            if not f.endswith(".niko"):
                continue
            path = os.path.join(d, f)
            src = open(path).read()
            # Pass the real path so relative imports resolve, like the CLI.
            errors = [
                x
                for x in analyze_source(src, path)
                if x.severity == "error"
            ]
            assert not errors, f"{f}: {errors}"
            checked += 1
    assert checked > 20


def test_niko1_regression_cases_sensible():
    """Spot checks on the Niko 1 suite: the advisor finds the real
    divergence in globals.niko and stays quiet on primes.niko."""
    cases = os.path.join(REPO, "tests", "cases")
    got = [
        (x.line, x.severity, x.code)
        for x in analyze_source(open(os.path.join(cases, "globals.niko")).read())
    ]
    assert got == [(4, "warning", "global-write")], got

    # primes.niko is idiomatic enough to migrate without a single finding.
    got = analyze_source(open(os.path.join(cases, "primes.niko")).read())
    assert got == [], got
