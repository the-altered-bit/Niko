"""Niko 2 interactive REPL (Alpha 20, VM only).

Prompts: ``niko> `` for a fresh submission, ``.... `` while a block is open.

Block continuation rule:
  A submitted line whose stripped form ends with ``:`` opens a block (a
  trailing colon inside a string literal does not count). While a block is
  open, every further line is appended to the same submission. The block
  ends when:

  - a blank line is entered (the block is submitted as-is), or
  - a line arrives indented at or below the level of a block opener
    (a dedent): open blocks close and a plain line starts a new
    submission -- EXCEPT lines starting with ``otherwise:`` or ``when ``,
    which continue the enclosing ``if``/``match`` construct.

  Nesting is tracked with an indent stack, so ``when`` arms inside a
  ``match`` dedent one level without closing the match. Indentation is
  measured in leading whitespace characters; spaces are expected.

Bare-expression echo: if the whole submission parses as a single
expression (``parse_expr``), it is evaluated and ``fmt(value)`` printed --
except ``nothing``, which is never echoed. Anything else runs as a
program: ``say`` prints, ``set``/``to`` are silent.

Imports: ``import "path/to/file.niko" as alias`` goes through the normal
module search path (``niko2/modules.py``); relative paths resolve against
the current working directory of the ``niko2 repl`` process. ``use "..."``
statements go through the same module pipeline (Alpha 22), so used files
work exactly like ``niko2 run``.

Colon commands (only at the primary prompt): ``:help``, ``:quit`` /
``:exit``, ``:reset``. Anything else starting with ``:`` gets a
plain-English hint.

Documented limits:
  - Diagnostics show cumulative session line numbers, not per-chunk ones.
  - A line ending in ``:`` inside a string literal is only detected with a
    quote-parity heuristic (no full string parsing).
  - Multi-line constructs must be typed with increasing indentation;
    the REPL cannot know a block is closed except by blank line/dedent.
  - ``:reset`` drops the VM, the globals and all history. ``:undo``
    drops just the last accepted chunk and rebuilds the session from
    the rest (as if the undone chunk had never been typed); there is
    no redo. Undo rebuilds interpreter state only: side effects
    outside the VM are replayed, not rewound (a kept chunk that
    writes a file writes it again; a kept chunk using ``ask`` prompts
    again during the rebuild).
  - Redefining a function replaces its nested helpers session-wide: a
    reference to the old function saved earlier resolves nested names to
    the newest definitions.
"""

# ---------------------------------------------------------------------------
# How REPL session state works (persistence approach).
#
# The REPL keeps every successfully parsed+typechecked chunk in
# `src_parts` as (user_text, effective) pairs -- the *effective* source
# for a bare expression the user typed is a synthetic
# `set __repl_echo_N to (<expr>)` line so it typechecks like any other
# statement, while the user text is kept so `:undo` can replay the
# session exactly as typed. Before a new chunk runs, the whole
# accumulated effective source is parsed and typechecked again --
# parse(src) for the program shape, then either check() (no imports) or
# the module pipeline (build_module_graph + per-unit checking +
# desugar_imports) when the session uses `import ... as ...`. This gives
# every chunk full knowledge of earlier definitions with no new syntax
# and no parser changes.
#
# Only the *delta* is compiled and executed: the desugared program is
# always [M result-slot pre-declares][M module wrappers][M init calls]
# [hoisted use bindings][entry statements...] (M = number of imported or
# used modules), so the new chunk is exactly the trailing entry statements
# after `self.entry_count`. Imported/used modules are initialized once per
# session: wrapper defs, pre-declares and init calls are re-emitted only
# for modules whose path is not yet in `self.initialized_modules`, so
# re-checking the accumulated source never re-runs a module body. One VM
# and one globals dict (`self.env`) live for the whole session; `:reset`
# drops them. A chunk that parses and typechecks joins the history even if
# it fails at runtime (like a script that crashes: earlier statements took
# effect); parse/type errors are rejected and never recorded.
#
# `:undo` pops the last pair and rebuilds: reset() back to a fresh VM,
# env and function table, then re-run the kept user chunks in order with
# stdout suppressed. The rebuild re-derives echo names (__repl_echo_N)
# and cumulative line numbers from the kept chunks, and each kept
# import/use initializes its module exactly once, so the rebuilt session
# behaves as if the undone chunk had never been typed.
# ---------------------------------------------------------------------------

from pathlib import Path
import contextlib
import io

from .parser import parse, parse_expr, ParseError
from .ast import Program
from .typecheck import check, TypeErrorNiko
from .compiler import compile_ast, CompileError
from .vm import VM
from .runtime import fmt, NikoRuntimeError
from .modules import (
    build_module_graph, check_units, desugar_imports, _has_imports,
    _has_uses, ImportErrorNiko,
)
from .cli import (
    _format_error, _format_parse_error,
)

try:
    import readline  # noqa: F401 -- history + line editing when available
except ImportError:  # graceful fallback: plain input()
    readline = None

REPL_NAME = '<repl>'
PROMPT = 'niko> '
CONT_PROMPT = '.... '
_ECHO_PREFIX = '__repl_echo_'
_CONT_KEYWORDS = ('otherwise:', 'when ')

HELP_TEXT = """Niko 2 interactive REPL (VM backend).
Type Niko 2 code as you would in a script. A line ending in ':' opens a
block; finish the block with a blank line or a dedented line. A bare
expression (e.g. 1 + 2) is evaluated and its value printed.
Relative imports resolve against the directory where you started the REPL.
Commands:
  :help    show this help
  :quit    leave the REPL (Ctrl-D works too)
  :exit    same as :quit
  :reset   forget everything typed so far
  :undo    drop the last chunk (as if it was never typed)"""


def _indent_of(line):
    return len(line) - len(line.lstrip(' \t'))


def _strip_comment(text):
    """Remove a trailing `#` comment, ignoring `#` inside string literals."""
    out = []
    q = None
    i = 0
    while i < len(text):
        ch = text[i]
        if q:
            out.append(ch)
            if ch == '\\' and i + 1 < len(text):
                out.append(text[i + 1])
                i += 2
                continue
            if ch == q:
                q = None
        elif ch in '"\'':
            q = ch
            out.append(ch)
        elif ch == '#':
            break
        else:
            out.append(ch)
        i += 1
    return ''.join(out)


def _opens_block(stripped):
    """Does this stripped line open an indented block?

    A trailing colon inside a string literal (``say "a:"`` can't end a
    line that way, but ``when "a:b":`` must still open) does not count:
    quote-parity heuristic, escapes honoured.
    """
    t = stripped.rstrip()
    if not t.endswith(':'):
        return False
    q = None
    i = 0
    body = t[:-1]
    while i < len(body):
        ch = body[i]
        if q:
            if ch == '\\':
                i += 2
                continue
            if ch == q:
                q = None
        elif ch in '"\'':
            q = ch
        i += 1
    return q is None


def _repl_imported_names(entry_tree):
    """Top-level defined names of the accumulated entry source, for
    forward references across chunks. `use`d names come from the module
    pipeline now (Alpha 22), not from a VMLoader-style walk."""
    return [n.name for n in entry_tree.body if hasattr(n, 'name')]


class ReplSession:
    """One interactive session: accumulated source, one VM, one globals dict."""

    def __init__(self):
        self._pending = None      # stashed line starting the next chunk
        self._eof_pending = False  # EOF arrived mid-block: submit, then exit
        self.reset()

    def reset(self):
        self.src_parts = []            # (user text, effective source) chunks
        self.entry_count = 0           # top-level stmts across src_parts
        self.initialized_modules = set()  # str(path) of modules already run
        self.env = {}
        self.vm = VM()
        self.echo_seq = 0
        # Cumulative function table: vm.run_module() replaces
        # vm._functions with each module's table, which would orphan nested
        # functions (MAKE_FUNCTION looks them up by qualname, e.g.
        # 'counter$bump') compiled in earlier chunks. Merging keeps every
        # chunk's functions resolvable for the life of the session.
        self._functions = {}

    @property
    def _entry_path(self):
        # Fake entry "file": relative imports resolve against the cwd,
        # which is the documented rule for the REPL.
        return Path.cwd() / REPL_NAME

    # -- input ---------------------------------------------------------

    def read_chunk(self):
        """Read one complete submission.

        Returns (kind, payload): 'code' with the chunk text, 'command'
        with the ':...' line, 'interrupt' (Ctrl-C, buffer discarded) or
        'eof' (Ctrl-D). A block left open at EOF is submitted first, then
        the following read reports 'eof'.
        """
        if self._eof_pending:
            self._eof_pending = False
            return ('eof', None)
        buf = []
        stack = []
        while True:
            if self._pending is not None:
                line, self._pending = self._pending, None
            else:
                try:
                    line = input(PROMPT if not buf else CONT_PROMPT)
                except EOFError:
                    if buf:
                        # Forgiving pipes: run the open block, then exit.
                        self._eof_pending = True
                        return ('code', '\n'.join(buf))
                    return ('eof', None)
                except KeyboardInterrupt:
                    print()
                    return ('interrupt', None)
            s = line.strip()
            if not buf:
                if s == '':
                    continue
                if s.startswith(':'):
                    return ('command', s)
            ind = _indent_of(line)
            if s == '':
                # Blank line ends an open block (at top level it was
                # already skipped above).
                if buf:
                    return ('code', '\n'.join(buf))
                continue
            while stack and ind <= stack[-1]:
                stack.pop()
            if buf and not stack and not s.startswith(_CONT_KEYWORDS):
                # Dedented plain line: the block is done; stash this line
                # as the start of the next chunk.
                self._pending = line
                return ('code', '\n'.join(buf))
            buf.append(line)
            if _opens_block(s):
                stack.append(ind)
            if not stack:
                return ('code', '\n'.join(buf))

    # -- commands ------------------------------------------------------

    def run_command(self, cmd):
        """Run a ':' command. Returns an exit code to leave, else None."""
        name = cmd[1:].strip().split()[0] if len(cmd) > 1 else ''
        if name in ('quit', 'exit'):
            return 0
        if name == 'help':
            print(HELP_TEXT)
            return None
        if name == 'reset':
            self.reset()
            print('Session cleared.')
            return None
        if name == 'undo':
            self.undo()
            return None
        print(f"Unknown command '{cmd}'. Type :help for the list of commands.")
        return None

    # -- undo ----------------------------------------------------------

    def undo(self):
        """Drop the last accepted chunk and rebuild the session.

        The kept chunks are re-run in order on a fresh VM and globals
        dict with their output suppressed, so the session behaves
        exactly as if the undone chunk had never been typed: module
        wrappers re-initialize once per the kept chunks (init-once
        preserved), echo names (``__repl_echo_N``) and cumulative line
        numbers are re-derived from the kept chunks, and names defined
        only by the undone chunk disappear. A kept chunk that somehow
        fails to replay (possible only if a module file changed under
        the session) is counted and reported rather than killing the
        session.
        """
        if not self.src_parts:
            print('Nothing to undo.')
            return
        user_text, _ = self.src_parts.pop()
        kept = [u for u, _ in self.src_parts]
        self.reset()
        failed = 0
        with contextlib.redirect_stdout(io.StringIO()):
            for chunk in kept:
                if not self.exec_chunk(chunk):
                    failed += 1
        preview = user_text.strip().splitlines()[0][:60]
        print(f'Undid chunk: {preview}')
        if failed:
            print(f'Warning: {failed} kept chunk(s) did not replay; '
                  f'session state may be incomplete.')

    # -- execution -----------------------------------------------------

    def _run(self, stmts):
        """Compile `stmts` and run them against the session VM/env.

        The compiled module's function table is merged into the session's
        cumulative table before running (see reset())."""
        module = compile_ast(Program(1, stmts))
        self._functions.update(module.functions)
        module.functions = self._functions
        self.vm.run_module(module, self.env)

    def exec_chunk(self, chunk):
        """Parse, check, compile and run one submission.

        Returns True when the chunk parsed and typechecked (it then joins
        the session history even if running it failed); False otherwise.
        All errors print with the usual caret diagnostics; tracebacks
        never escape.
        """
        text = chunk.strip()
        if not text:
            return True
        # Bare-expression echo: try the chunk as one expression first.
        expr_src = _strip_comment(text).strip()
        try:
            if not expr_src:
                raise ParseError('empty', 1)
            parse_expr(expr_src, 1)
            is_echo = True
        except Exception:
            is_echo = False
        echo_name = None
        if is_echo:
            echo_name = f'{_ECHO_PREFIX}{self.echo_seq}'
            self.echo_seq += 1
            effective = f'set {echo_name} to ({expr_src})'
        else:
            effective = chunk

        check_src = '\n'.join([eff for _, eff in self.src_parts] + [effective])
        try:
            entry_tree = parse(check_src)
        except ParseError as e:
            print(_format_parse_error(e, check_src, REPL_NAME))
            return False
        try:
            if _has_imports(entry_tree) or _has_uses(entry_tree):
                graph = build_module_graph(self._entry_path, entry_tree)
                check_units(graph, _repl_imported_names(entry_tree))
                prepared = desugar_imports(graph)
            else:
                graph = None
                check(entry_tree, _repl_imported_names(entry_tree))
                prepared = entry_tree
        except (TypeErrorNiko, ImportErrorNiko) as e:
            print(_format_error(e, check_src, REPL_NAME))
            return False

        # Slice the delta out of the desugared program. Layout is always
        # [M pre-declares][M wrappers][M init calls][entry stmts...]
        # (M = imported + used modules; the entry section starts with the
        # hoisted `use` bindings), and entry statements stay in source
        # order, so the new chunk is the trailing entry statements after
        # self.entry_count.
        n_mods = len(graph) - 1 if graph else 0
        entry_stmts = prepared.body[3 * n_mods:]
        new_entry = entry_stmts[self.entry_count:]
        delta_prefix = []
        new_unit_paths = []
        if graph:
            for i, unit in enumerate(graph[:-1]):
                p = str(unit.path)
                if p not in self.initialized_modules:
                    delta_prefix.extend((prepared.body[i],
                                         prepared.body[n_mods + i],
                                         prepared.body[2 * n_mods + i]))
                    new_unit_paths.append(p)
        try:
            if delta_prefix:
                # Initialize newly-imported/used modules exactly once.
                self._run(delta_prefix)
                self.initialized_modules.update(new_unit_paths)
            if new_entry:
                self._run(new_entry)
        except (CompileError, NikoRuntimeError) as e:
            print(_format_error(e, check_src, REPL_NAME))
        except Exception as e:  # last resort: the loop never dies
            print(f'Niko error: internal {type(e).__name__}: {e}')

        # Parse+check passed, so the chunk joins the history even if it
        # failed at runtime (script-that-crashed semantics). The raw
        # user text is kept alongside the effective source so `:undo`
        # can replay the session exactly as typed.
        self.src_parts.append((chunk, effective))
        self.entry_count = len(entry_stmts)

        if is_echo:
            v = self.env.pop(echo_name, None)
            if v is not None:  # `nothing` is never echoed
                print(fmt(v))
        return True


def main():
    """Run the REPL. Returns the process exit code."""
    sess = ReplSession()
    print('Niko 2 REPL (VM). Type :help for help; :quit or Ctrl-D to leave.')
    while True:
        kind, payload = sess.read_chunk()
        if kind == 'eof':
            print()
            return 0
        if kind == 'interrupt':
            continue
        if kind == 'command':
            rc = sess.run_command(payload)
            if rc is not None:
                return rc
            continue
        try:
            sess.exec_chunk(payload)
        except KeyboardInterrupt:
            print()
        except Exception as e:  # the loop never dies
            print(f'Niko error: internal {type(e).__name__}: {e}')
