#!/usr/bin/env python3
"""Alpha 27: tests for `niko2 test` (niko2/test_runner.py).

Covers discovery (both filename conventions, nested dirs, non-test
files ignored, hidden dirs skipped), pass/fail reporting (plain
greppable lines, summary counts, exit codes), per-test isolation (fresh
VM per test -- a global mutated in one test is invisible in the next),
uncompilable files (exactly one failure entry, never a crash), file:line
diagnostics, `say` pass-through, `import` of the code under test, and a
real `python -m niko2 test` subprocess run.

Tests that need the `assert` statement are marked NEEDS_ASSERT and skip
until the sibling Alpha 27 worker lands it; the runner itself never
parses assert output, so they only assert on PASS/FAIL lines and the
failure text. Their source snippets assume the contract's syntax
(`assert <expr>` / `assert <expr>, "message"`); if the sibling picks a
different surface syntax, only the ASSERT_* constants below change.

Hermetic: everything runs in pytest's tmp_path with monkeypatched cwd;
the subprocess test sandboxes HOME and PYTHONPATH like test_repl.py.
No network is used.

Run:  python3 -m pytest tests/test_test_runner.py -q
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

root = Path(__file__).resolve().parent
project_root = root.parent
sys.path.insert(0, str(project_root))

from niko2 import test_runner
from niko2.parser import parse, ParseError


def _assert_supported():
    try:
        parse('assert 1 == 1\n')
        return True
    except ParseError:
        return False


NEEDS_ASSERT = pytest.mark.skipif(
    not _assert_supported(),
    reason='assert statement not implemented yet (Alpha 27 sibling worker)')

# Assumed assert syntax, per the sibling worker's contract:
#   assert <expr>            -- fails naming the expression
#   assert <expr>, "message"  -- fails with the user message
ASSERT_PASS_SRC = 'to test_a:\n  assert 1 + 1 == 2\n'
ASSERT_FAIL_SRC = 'to test_a:\n  assert 1 + 1 == 3\n'
ASSERT_MSG_SRC = 'to test_a:\n  assert 1 == 2, "custom failure message"\n'


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf8')
    return path


PASSING = 'to test_ok:\n  say "fine"\n'
# Pre-assert failure idiom: unwrap(error("msg")) raises NikoRuntimeError.
FAILING = 'to test_ok:\n  say "fine"\n\nto test_bad:\n  unwrap(error("boom message"))\n'


# --- discovery ---------------------------------------------------------

def test_discovery_both_conventions_and_nested(workdir):
    _write(workdir / 'a_test.niko', PASSING)
    _write(workdir / 'test_b.niko', PASSING)
    _write(workdir / 'sub' / 'test_c.niko', PASSING)
    _write(workdir / 'sub' / 'helper.niko', 'say "not a test file"\n')
    _write(workdir / 'notes.txt', 'not niko\n')
    _write(workdir / '.hidden' / 'd_test.niko', PASSING)
    found = test_runner.discover_test_files(workdir)
    rel = sorted(str(p.relative_to(workdir)) for p in found)
    assert rel == ['a_test.niko', 'sub/test_c.niko', 'test_b.niko']


def test_discovery_ignores_everything_else(workdir):
    _write(workdir / 'main.niko', 'say "hi"\n')
    assert test_runner.discover_test_files(workdir) == []


def test_no_test_files_found(workdir, capsys):
    code = test_runner.main()
    out = capsys.readouterr().out
    assert code == 0
    assert 'No test files found' in out


def test_missing_target(workdir, capsys):
    code = test_runner.main('does-not-exist')
    assert code == 2
    assert 'no such file or directory' in capsys.readouterr().out


def test_single_file_target(workdir, capsys):
    f = _write(workdir / 'solo_test.niko', PASSING)
    code = test_runner.main(str(f))
    out = capsys.readouterr().out
    assert code == 0
    assert 'PASS test_ok (solo_test.niko)' in out
    assert '1 passed, 0 failed' in out


# --- reporting ----------------------------------------------------------

def test_pass_fail_summary_and_exit_code(workdir, capsys):
    _write(workdir / 'm_test.niko', FAILING)
    code = test_runner.main()
    out = capsys.readouterr().out
    assert code == 1
    assert 'PASS test_ok (m_test.niko)' in out
    assert 'FAIL test_bad (m_test.niko)' in out
    assert 'boom message' in out            # the failure's message
    assert 'm_test.niko' in out             # the file it came from
    assert '1 passed, 1 failed' in out
    assert '\x1b[' not in out               # no color codes


def test_all_pass_exit_zero(workdir, capsys):
    _write(workdir / 'ok_test.niko', PASSING)
    code = test_runner.main()
    out = capsys.readouterr().out
    assert code == 0
    assert '1 passed, 0 failed' in out


def test_say_output_passes_through(workdir, capsys):
    _write(workdir / 's_test.niko', 'to test_say:\n  say "hello from test"\n')
    code = test_runner.main()
    out = capsys.readouterr().out
    assert code == 0
    assert 'hello from test' in out
    assert 'PASS test_say (s_test.niko)' in out


def test_non_test_functions_ignored(workdir, capsys):
    src = ('to helper:\n  say "not a test"\n\n'
           'to test_real:\n  say "real"\n\n'
           'to testing_thing with x:\n  say x\n')
    _write(workdir / 'n_test.niko', src)
    code = test_runner.main()
    out = capsys.readouterr().out
    assert code == 0
    assert 'PASS test_real (n_test.niko)' in out
    assert 'helper' not in out
    assert '1 passed, 0 failed' in out


# --- isolation ----------------------------------------------------------

def test_global_mutation_does_not_leak_between_tests(workdir, capsys):
    # `put ... in` mutates the list in place. If the two tests shared a
    # VM, test_second would see 99 and fail; with a fresh VM per test,
    # `seen` is [] again and both pass.
    src = ('set seen to []\n'
           'to test_first:\n'
           '  put 99 in seen\n'
           'to test_second:\n'
           '  if 99 is in seen:\n'
           '    unwrap(error("state leaked between tests"))\n')
    _write(workdir / 'iso_test.niko', src)
    code = test_runner.main()
    out = capsys.readouterr().out
    assert code == 0, out
    assert '2 passed, 0 failed' in out


def test_top_level_code_reruns_per_test(workdir, capsys):
    src = ('set calls to []\n'
           'put 1 in calls\n'
           'to test_each_sees_fresh_state:\n'
           '  if calls != [1]:\n'
           '    unwrap(error("top-level state leaked"))\n')
    _write(workdir / 'fresh_test.niko', src)
    assert test_runner.main() == 0


# --- compile failures ----------------------------------------------------

def test_uncompilable_file_is_one_failure_entry(workdir, capsys):
    src = ('to test_a:\n'
           '  say "a"\n'
           'this is not valid niko (((\n'
           'to test_b:\n'
           '  say "b"\n')
    _write(workdir / 'bad_test.niko', src)
    code = test_runner.main()
    out = capsys.readouterr().out
    assert code == 1
    # Exactly one FAIL line for the whole file, never one per test.
    assert out.count('FAIL') == 1
    assert 'bad_test.niko' in out and 'does not compile' in out
    assert '0 passed, 1 failed' in out


def test_type_error_reports_file_and_line(workdir, capsys):
    # The checker rejects the unknown name on line 2, so the base compile
    # fails and the file is a single failure entry with the real file:line
    # caret diagnostic.
    src = 'to test_a:\n  frobnicate()\n'
    _write(workdir / 'terr_test.niko', src)
    code = test_runner.main()
    out = capsys.readouterr().out
    assert code == 1
    assert 'FAIL terr_test.niko (does not compile)' in out
    assert 'Niko error in terr_test.niko' in out
    assert 'line 2' in out


def test_per_test_runtime_error_shows_file_and_line(workdir, capsys):
    # `1 + "s"` slips past the checker and blows up in the VM, which tags
    # the error with the source line -- the per-test failure entry shows
    # the error with file:line, like `niko2 run` does.
    src = 'to test_a:\n  set x to 1 + "s"\n'
    _write(workdir / 'rtline_test.niko', src)
    code = test_runner.main()
    out = capsys.readouterr().out
    assert code == 1
    assert 'FAIL test_a (rtline_test.niko)' in out
    assert 'Niko error in rtline_test.niko' in out
    assert 'Line 2' in out


def test_runtime_error_shows_file_and_message(workdir, capsys):
    src = 'to test_a:\n  set x to 1 / 0\n'
    _write(workdir / 'rt_test.niko', src)
    code = test_runner.main()
    out = capsys.readouterr().out
    assert code == 1
    assert 'FAIL test_a (rt_test.niko)' in out
    assert 'Niko error in rt_test.niko' in out
    assert 'divide by zero' in out


# --- imports of the code under test --------------------------------------

def test_import_of_code_under_test(workdir, capsys):
    _write(workdir / 'calc.niko',
           'to add with a, b:\n  give back a + b\n')
    _write(workdir / 'calc_test.niko',
           'import "calc.niko" as calc\n\n'
           'to test_add:\n'
           '  if calc.add(20, 22) != 42:\n'
           '    unwrap(error("add is broken"))\n')
    code = test_runner.main()
    out = capsys.readouterr().out
    assert code == 0, out
    assert 'PASS test_add (calc_test.niko)' in out


# --- test shape verdict ---------------------------------------------------

def test_space_form_is_not_collected(workdir):
    # `to test foo:` parses (a function literally named 'test foo') but
    # can never be called, so the runner must not treat it as a test.
    f = _write(workdir / 'sp_test.niko', 'to test foo:\n  say 1\n')
    assert test_runner.collect_test_names(f) == []
    assert test_runner.run_test_file(f) == []


def test_param_functions_not_collected(workdir):
    f = _write(workdir / 'p_test.niko',
               'to test_with_param with x:\n  say x\n')
    assert test_runner.collect_test_names(f) == []


# --- assert-based tests (need the sibling worker's change) ----------------

@NEEDS_ASSERT
def test_assert_pass(workdir, capsys):
    _write(workdir / 'a_test.niko', ASSERT_PASS_SRC)
    code = test_runner.main()
    out = capsys.readouterr().out
    assert code == 0, out
    assert 'PASS test_a (a_test.niko)' in out


@NEEDS_ASSERT
def test_assert_fail_names_expression(workdir, capsys):
    _write(workdir / 'a_test.niko', ASSERT_FAIL_SRC)
    code = test_runner.main()
    out = capsys.readouterr().out
    assert code == 1
    assert 'FAIL test_a (a_test.niko)' in out
    assert '1 + 1 == 3' in out  # the failed expression is named


@NEEDS_ASSERT
def test_assert_with_message(workdir, capsys):
    _write(workdir / 'a_test.niko', ASSERT_MSG_SRC)
    code = test_runner.main()
    out = capsys.readouterr().out
    assert code == 1
    assert 'FAIL test_a (a_test.niko)' in out
    assert 'custom failure message' in out


# --- real CLI end to end ---------------------------------------------------

def _run_cli(args, cwd, home):
    e = dict(os.environ)
    e['PYTHONPATH'] = str(project_root)
    e['HOME'] = str(home)
    return subprocess.run(
        [sys.executable, '-m', 'niko2'] + args,
        capture_output=True, text=True, cwd=cwd, env=e, timeout=60)


def test_cli_end_to_end(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    proj = tmp_path / 'proj'
    proj.mkdir()
    (proj / 'ok_test.niko').write_text(PASSING, encoding='utf8')
    r = _run_cli(['test'], str(proj), home)
    assert r.returncode == 0, r.stdout + r.stderr
    assert 'PASS test_ok (ok_test.niko)' in r.stdout
    assert '1 passed, 0 failed' in r.stdout

    (proj / 'bad_test.niko').write_text(FAILING, encoding='utf8')
    r = _run_cli(['test'], str(proj), home)
    assert r.returncode == 1, r.stdout + r.stderr
    assert 'FAIL test_bad (bad_test.niko)' in r.stdout
    assert '2 passed, 1 failed' in r.stdout

    empty = tmp_path / 'empty'
    empty.mkdir()
    r = _run_cli(['test'], str(empty), home)
    assert r.returncode == 0
    assert 'No test files found' in r.stdout
