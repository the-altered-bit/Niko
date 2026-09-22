#!/usr/bin/env python3
"""Alpha 20: interactive REPL (`niko2 repl`) tests.

Drives the REPL non-interactively: spawns `python3 -m niko2 repl` as a
subprocess with scripted stdin and asserts on stdout. Covers session
persistence across chunks, multi-line blocks, bare-expression echo,
error recovery (checker + runtime), colon commands, stdlib and local
imports (incl. init-once semantics), cross-chunk closures, and EOF
behavior.

Hermetic by construction: every run happens in a throwaway cwd, HOME is
sandboxed so nothing can touch the real ~, and PYTHONPATH points at the
repo root. No network is used.

Run:  python3 tests/test_repl.py
"""
import os
import pathlib
import subprocess
import sys
import tempfile

root = pathlib.Path(__file__).resolve().parent
project_root = root.parent
sys.path.insert(0, str(project_root))

# One throwaway sandbox for the whole script.
SESSION = pathlib.Path(tempfile.mkdtemp(prefix='niko-repltest-'))
HOME = SESSION / 'home'
HOME.mkdir()
BANNER = 'Niko 2 REPL (VM). Type :help for help; :quit or Ctrl-D to leave.'
PASS_COUNT = 0


def _run_repl(script, cwd=None, timeout=30):
    """Feed `script` to the REPL's stdin, return the CompletedProcess."""
    e = dict(os.environ)
    e['PYTHONPATH'] = str(project_root)
    e['HOME'] = str(HOME)
    d = SESSION if cwd is None else cwd
    d.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        [sys.executable, '-m', 'niko2', 'repl'],
        input=script, capture_output=True, text=True,
        cwd=d, env=e, timeout=timeout)


def _clean(out):
    """Strip prompts and the banner, yielding the REPL's own output."""
    s = out.replace(BANNER, '')
    s = s.replace('niko> ', '').replace('.... ', '')
    return s


def check(name, script, needles=(), absent=(), cwd=None, count=None):
    """Run one scripted session; assert needles present, absent missing,
    and (optionally) exact occurrence count of `count[0]` == `count[1]`."""
    global PASS_COUNT
    r = _run_repl(script, cwd=cwd)
    assert r.returncode == 0, f'{name}: exit {r.returncode}\n{r.stdout!r}\n{r.stderr!r}'
    body = _clean(r.stdout)
    for n in needles:
        assert n in body, f'{name}: missing {n!r} in:\n{body!r}'
    for n in absent:
        assert n not in body, f'{name}: unexpected {n!r} in:\n{body!r}'
    if count is not None:
        needle, want = count
        got = body.count(needle)
        assert got == want, f'{name}: {needle!r} x{got}, want x{want}:\n{body!r}'
    PASS_COUNT += 1
    print(f'  ok {name}')


def test_banner_and_help():
    global PASS_COUNT
    r = _run_repl(':quit\n')
    assert r.returncode == 0, f'banner run exit {r.returncode}'
    assert BANNER in r.stdout, f'banner missing in:\n{r.stdout!r}'
    PASS_COUNT += 1
    print('  ok banner')
    check('help', ':help\n:quit\n',
          needles=['Commands:', ':help', ':quit', ':reset'])


def test_persistence():
    check('persistence', 'set x to 5\nsay x + 1\n:quit\n', needles=['6'])


def test_multiline_function():
    check('multiline-fn',
          'to add with a, b:\n    give back a + b\n\nsay add(2, 3)\n:quit\n',
          needles=['5'])


def test_echo():
    # Bare expressions echo; `nothing` never echoes.
    check('echo',
          '1 + 2\n"hi"\n[1, 2]\nnothing\nsay nothing\n:quit\n',
          needles=['3', 'hi', '[1, 2]', 'nothing'],
          count=('nothing', 1))  # only the `say nothing` statement


def test_echo_comment():
    # Comments are stripped before the echo parse; '#' inside strings survives.
    check('echo-comment', '1 + 2 # three\n"a # b"\n:quit\n',
          needles=['3', 'a # b'])


def test_type_error_recovery():
    # A checker error prints a caret diagnostic; the session survives.
    check('type-error',
          'set x to undefined_name\nsay "alive"\n:quit\n',
          needles=['unknown name "undefined_name"', '^', 'alive'])


def test_runtime_error_recovery():
    # A runtime error prints a Niko error; the session survives.
    check('runtime-error',
          'set s to "abc"\nsay s + 1\nsay "survived"\n:quit\n',
          needles=['can only concatenate', 'survived'])


def test_if_block_dedent():
    # A dedented line ends the block and starts the next chunk.
    check('if-dedent',
          'if yes:\n    say "yup"\nsay "after"\n:quit\n',
          needles=['yup', 'after'])


def test_match_block():
    # `otherwise:`/`when ` continue the enclosing match across dedents.
    check('match-block',
          'set s to "abc"\n'
          'match s:\n'
          '    when "abc":\n'
          '        say "matched"\n'
          '    otherwise:\n'
          '        say "other"\n'
          '\n'
          'say "after match"\n'
          ':quit\n',
          needles=['matched', 'after match'], absent=['other'])


def test_quit_and_unknown_command():
    check('quit', ':quit\n', needles=[])
    r = _run_repl(':quit\n')
    assert r.returncode == 0, f'quit exit {r.returncode}'
    global PASS_COUNT
    PASS_COUNT += 1
    print('  ok quit-exit-0')
    check('unknown-command', ':frobnicate\n:quit\n',
          needles=["Unknown command ':frobnicate'"])


def test_reset():
    check('reset',
          'set x to 5\n:reset\nsay x\n:quit\n',
          needles=['Session cleared.', 'unknown name "x"'])


def test_stdlib_import():
    check('stdlib-import',
          'import "stdlib/text.niko" as text\n'
          'say text.capitalize("hi")\n'
          ':quit\n',
          needles=['Hi'])


def test_module_init_once():
    # A module body with top-level `say` runs exactly once per session,
    # even when imported again under a different alias in a later chunk.
    d = SESSION / 'modtest'
    d.mkdir(parents=True, exist_ok=True)
    (d / 'm1.niko').write_text('say "loaded-once"\nset tag to "m1-tag"\n')
    check('module-init-once',
          'import "m1.niko" as a\n'
          'import "m1.niko" as b\n'
          'say a.tag\n'
          ':quit\n',
          needles=['loaded-once', 'm1-tag'],
          count=('loaded-once', 1),
          cwd=d)


def test_cross_chunk_closure():
    # The cumulative function table keeps nested helpers resolvable
    # across chunks (regression test for the run_module() fix).
    check('cross-chunk-closure',
          'to counter:\n'
          '    set n to 10\n'
          '    to bump:\n'
          '        set n to n + 1\n'
          '        give back n\n'
          '    give back bump\n'
          '\n'
          'set c to counter()\n'
          'say c()\n'
          'say c()\n'
          ':quit\n',
          needles=['11', '12'])


def test_eof_exit_zero():
    r = _run_repl('set x to 1\n')
    assert r.returncode == 0, f'EOF exit {r.returncode}'
    assert '1' not in _clean(r.stdout)  # set is silent
    global PASS_COUNT
    PASS_COUNT += 1
    print('  ok eof-exit-0')


def test_eof_mid_block():
    # An open block at EOF is submitted and run before exiting 0.
    r = _run_repl('if yes:\n    say "eof-block-ran"\n')
    assert r.returncode == 0, f'EOF-mid-block exit {r.returncode}'
    assert 'eof-block-ran' in _clean(r.stdout), r.stdout
    global PASS_COUNT
    PASS_COUNT += 1
    print('  ok eof-mid-block')


def test_error_chunk_not_recorded():
    # A chunk that fails typechecking never joins the history: a later
    # chunk redefining the same name starts clean.
    check('error-not-recorded',
          'set n: number to "bad"\nset n to 42\nsay n\n:quit\n',
          needles=['cannot assign text to number', '42'])


def test_use_in_repl():
    # Alpha 22: `use` goes through the shared module pipeline in the REPL
    # too. A used file's names are visible in later chunks, and its
    # top-level code runs exactly once per session.
    d = SESSION / 'usetest'
    d.mkdir(parents=True, exist_ok=True)
    (d / 'helper.niko').write_text(
        'say "helper-loaded"\nset who to "helper-who"\n'
        'to greet with n:\n    give back "hi " + n\n')
    check('use-in-repl',
          'use "helper.niko"\n'
          'say who\n'
          'say greet("Casper")\n'
          'use "helper.niko"\n'
          'say who\n'
          ':quit\n',
          needles=['helper-loaded', 'helper-who', 'hi Casper'],
          count=('helper-loaded', 1),
          cwd=d)


if __name__ == '__main__':
    try:
        test_banner_and_help()
        test_persistence()
        test_multiline_function()
        test_echo()
        test_echo_comment()
        test_type_error_recovery()
        test_runtime_error_recovery()
        test_if_block_dedent()
        test_match_block()
        test_quit_and_unknown_command()
        test_reset()
        test_stdlib_import()
        test_module_init_once()
        test_cross_chunk_closure()
        test_eof_exit_zero()
        test_eof_mid_block()
        test_error_chunk_not_recorded()
        test_use_in_repl()
    except AssertionError as e:
        print(f'FAIL: {e}')
        sys.exit(1)
    print(f'REPL tests green: {PASS_COUNT} checks')
