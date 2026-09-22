#!/usr/bin/env python3
"""Alpha 27: `niko2 test` -- a minimal test runner for Niko projects.

Test shape
----------
A test is a top-level function with no parameters whose name starts with
``test_``::

    to test_add:
        assert add(2, 3) == 5

The ``to test <name>:`` (space) form is deliberately NOT a test shape:
the parser accepts it as a function literally named ``'test <name>'``,
but no call syntax can ever invoke it (``test foo()`` is a ParseError),
so it can never run. Only the ``test_*`` identifier form is collected.

Discovery
---------
``niko2 test [dir]`` (default: the current directory) recursively finds
files named ``*_test.niko`` or ``test_*.niko`` and runs every ``test_*``
function in each of them. A single ``.niko`` file may also be passed to
run just that file. Hidden directories (``.git``, ``.niko``, ...) are
skipped. There is no config file.

Execution model
---------------
For each test, the runner compiles ``<file source> + "\\n<testname>()\\n"``
through the normal module pipeline (``cli.compile_source``) and executes
it in a brand-new ``VM``. Consequences:

* ``import``/``use`` of the code under test work exactly like
  ``niko2 run`` -- the test file is an ordinary Niko program.
* Tests are totally isolated: fresh globals, fresh VM, fresh compiled
  module per test. A global mutated in one test is invisible in the next.
* The invocation line is appended at the *end*, so the test bodies keep
  their original line numbers and diagnostics point at the real file
  lines (parsing is line-oriented; only appended lines move).
* A file that does not compile on its own is reported as exactly one
  failure entry; its tests never run. The runner never crashes on a bad
  test file.

Only the VM backend is used. This is a dev tool and the VM is the
reference semantics; WASM/native are compile targets, and spinning up a
toolchain plus a host runtime per test would be slow and could not easily
invoke individual test functions.

``say`` output inside tests prints straight through to stdout (simplest,
and there is no output-capture assertion API that capturing would serve).

Reporting is plain text with no color codes, greppable: one
``PASS <test> (<file>)`` / ``FAIL <test> (<file>)`` line per test, the
CLI-style ``Niko error in <file>: line L, column C`` diagnostic (with
caret) indented under each FAIL, and a final ``N passed, M failed``
summary. Exit code is 0 iff every test passed. When no test files are
found, a message is printed and the exit code is 0.
"""

from pathlib import Path

from .cli import compile_source, _format_error, _format_parse_error
from .compiler import compile_ast, CompileError
from .parser import parse, ParseError
from .typecheck import TypeErrorNiko
from .modules import ImportErrorNiko
from .runtime import NikoRuntimeError
from .vm import VM

TEST_PREFIX = 'test_'

# Everything the compile pipeline can raise for a bad test file.
_COMPILE_ERRORS = (ParseError, TypeErrorNiko, CompileError, ImportErrorNiko)
# ...plus what executing a test can raise.
_RUN_ERRORS = (TypeErrorNiko, CompileError, NikoRuntimeError, ImportErrorNiko)


def _is_test_file(path):
    name = path.name
    return name.startswith('test_') or name.endswith('_test.niko')


def discover_test_files(root):
    """Recursively find ``*_test.niko`` / ``test_*.niko`` under ``root``.

    ``root`` may also be a single ``.niko`` file, which is then the only
    candidate. Results are sorted for deterministic output.
    """
    root = Path(root)
    if root.is_file():
        return [root] if _is_test_file(root) else []
    found = []
    for path in sorted(root.rglob('*.niko')):
        if any(part.startswith('.') for part in path.parts):
            continue  # skip .git, .niko package cache, etc.
        if _is_test_file(path):
            found.append(path)
    return found


def collect_test_names(path):
    """Top-level ``to test_<name>:`` functions with no params, in order.

    Raises ParseError if the file does not parse.
    """
    tree = parse(Path(path).read_text(encoding='utf8'))
    names = []
    for node in tree.body:
        if (node.__class__.__name__ == 'FunctionDef'
                and node.name.startswith(TEST_PREFIX)
                and not node.params):
            names.append(node.name)
    return names


def _run_one_test(path, src, test_name):
    """Compile ``src`` + an invocation line; run it in a fresh VM.

    Returns ``(ok, detail)``; ``detail`` is the CLI-style diagnostic
    text on failure, ``''`` on success.
    """
    name = str(path)
    sep = '' if src.endswith('\n') else '\n'
    # Appended at the end: body line numbers are undisturbed.
    src2 = f'{src}{sep}{test_name}()\n'
    try:
        tree = compile_source(src2, name)
        VM().run_module(compile_ast(tree), {})
    except ParseError as e:
        return False, _format_parse_error(e, src2, name)
    except _RUN_ERRORS as e:
        return False, _format_error(e, src2, name)
    except Exception as e:  # never let one bad test crash the runner
        return False, f'Niko error in {name}: unexpected {type(e).__name__}: {e}'
    return True, ''


def run_test_file(path):
    """Run every test in one file.

    Returns a list of ``(label, ok, detail)`` rows. A file that does not
    compile yields exactly one failing row (``label == 'file'``); its
    tests never run.
    """
    path = Path(path)
    name = str(path)
    try:
        src = path.read_text(encoding='utf8')
    except OSError as e:
        return [('file', False, f'Niko error in {name}: {e}')]
    try:
        names = collect_test_names(path)
    except ParseError as e:
        return [('file', False, _format_parse_error(e, src, name))]
    try:
        # The file must compile on its own; a broken file is one failure
        # entry, not one per test.
        compile_source(src, name)
    except ParseError as e:
        return [('file', False, _format_parse_error(e, src, name))]
    except _COMPILE_ERRORS as e:
        return [('file', False, _format_error(e, src, name))]
    except Exception as e:  # never crash on a bad test file
        return [('file', False,
                 f'Niko error in {name}: unexpected {type(e).__name__}: {e}')]
    return [(test_name, ok, detail)
            for test_name in names
            for (ok, detail) in [_run_one_test(path, src, test_name)]]


def _display(path):
    try:
        return str(Path(path).relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def main(target=None):
    """Entry point for ``niko2 test [dir]``. Returns the exit code."""
    root = Path(target) if target else Path('.')
    if not root.exists():
        print(f'Niko error: no such file or directory: {target}')
        return 2
    files = discover_test_files(root)
    if not files:
        print('No test files found (*_test.niko, test_*.niko).')
        return 0
    passed = failed = 0
    for path in files:
        rel = _display(path)
        for label, ok, detail in run_test_file(path):
            tag = (f'FAIL {rel} (does not compile)' if label == 'file'
                   else f'{"PASS" if ok else "FAIL"} {label} ({rel})')
            print(tag)
            if not ok:
                for line in detail.splitlines():
                    print('  ' + line)
            if ok:
                passed += 1
            else:
                failed += 1
    print(f'{passed} passed, {failed} failed')
    return 0 if failed == 0 else 1
