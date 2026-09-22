# Alpha 23 design: module-aware editor diagnostics

Goal: make the language server's live diagnostics understand multi-file
programs. Up to now the LSP's analysis ran the plain single-file
`check()`: `import "lib/math.niko" as m` / `m.add(1, 2)` got a phantom
`unknown name "m"` squiggle, and names merged in by `use` were flagged
too (the Alpha 22 known gap). Go-to-definition and hover already
followed imports (Alpha 17/21/22); diagnostics now use the module graph
as well. Two small fixes ride along: the debugger's same-line
breakpoint re-fire (a KNOWN_LIMITATIONS bullet since Alpha 22),
`niko2 format`'s doubled quotes on `use` (Alpha 22 known gap), and the
`use`-inside-a-function silent ignore (Alpha 22 known gap).

## Design (as implemented)

### 1. Module-aware LSP diagnostics (`niko2/lsp.py`, `niko2/modules.py`)

New `_analyze(text, entry_path=None, source_overrides=None)` in
`lsp.py`:

- **Fast path (unchanged, zero disk reads).** If the open document has
  no top-level `import`/`use` statements, or has no file path, it uses
  the old single-file `check()` exactly as before. Single-file
  documents do no disk IO — this is the same performance boundary as
  every previous alpha.
- **Module path.** Parses the entry, runs
  `build_module_graph(entry_path, entry_tree=tree, source_overrides=...)`
  + `check_units(graph)`. Returns `({abs_path: [diagnostics]},
  entry_tree)`. Diagnostics use the original per-file line numbers
  from each `check_units` unit.
- **Error attribution.** `ImportErrorNiko` (unresolvable import/use,
  cycles) becomes exactly one diagnostic on the offending line, no
  cascade. `TypeErrorNiko` is re-tagged with `e.path` and attributed
  to the right file and line, so the squiggle lands in the module
  file, not the entry.
- **New `_lint_alias_attrs(graph)` (LSP-local, read-only).** The
  shared checker types import aliases as `map`, so `m.nope` would go
  silent without this. The lint walks the entry tree for `alias.attr`
  attribute accesses and flags any attr not in the module's exports
  (via `_module_unit_exports`, using `find_symbol` kind=='module' to
  skip shadowed locals). Message: `unknown attribute "nope" on module
  "m"`.
- **New `source_overrides` in `build_module_graph`
  (`niko2/modules.py`).** Maps resolved Path → parsed tree so
  open-but-unsaved module docs override disk reads. All existing
  callers (`cli.py`, `debug.py`, `repl.py`) use positional args, so the
  new keyword param is backwards-compatible.
- **Unsaved-module staleness fix** (caught by the tests during this
  alpha). `_notify_diagnostics` previously used a global
  `self._published_diags` set, so an analysis rooted at module file B
  would clear diagnostics owned by file A's analysis. Replaced with
  per-owner tracking: `self._diags_owner = {uri: root_uri}` — an
  analysis only clears diagnostics its own document previously
  published.
- **Read-only.** Diagnostics never write files and never hit the
  network (package imports resolve from the local cache only).
- **Performance boundary.** Single-file documents do no disk IO
  (unchanged). With modules, each didChange parses the entry + other
  open docs (only when the entry needs the module pipeline) and
  reads/parses every reachable module, then typechecks each unit. No
  desugaring, no backend compilation. Future work: cache module parses
  by mtime. (Also in `niko2/KNOWN_LIMITATIONS.md`.)

### 2. Debugger same-line breakpoint re-fire fix (`niko2/debug.py`)

**Problem** (a KNOWN_LIMITATIONS bullet since Alpha 22). A breakpoint
on a call line (e.g. `set r to bump(10)`) re-fired when the call
returned: every instruction of one statement carries the statement's
line number, and the VM's `on_ins` hook dedupes on `(id(frame),
line)`. After the breakpoint fired at the CALL instruction and the
user stepped in/out (or continued), the post-call STORE was a *new*
key on the same line, and the still-armed breakpoint fired again for
the same statement visit. The `elif` chain also let the breakpoint win
over a completing step.

**Fix.** `self._last_bp_stop = (frame, path, line, ins)` — set on
every real breakpoint stop, holding strong refs to the actual frame
and instruction objects (so no `id()`-reuse hazard; compared with
`is`). In `_on_ins`, when an armed breakpoint's condition holds but
the stop is the same frame/path/line with a *different* instruction
object → suppress the breakpoint (`bp_suppressed`) and let a pending
step complete instead. Precedence otherwise preserved exactly:
armed-but-false-condition still swallows steps (guarded by `not
(bp_line and not bp_suppressed)`), and `set_breakpoints` clears the
memory (fail-open, so no stale suppression).

**Why it's safe:**

- Loop iterations re-execute the *same* instruction objects → `last[3]
  is ins` → breakpoints still re-fire once per iteration (covered by
  the existing DAP test's 3-hit repeat loop; manually verified 3
  stops + termination).
- The memory is per-frame-object, so a `use`-wrapper recursion would
  not confuse visits across frames.
- Conditional-breakpoint semantics are unchanged (condition evaluated
  first, as before).

### 3. Formatter `use` quotes (`niko2/formatter.py`)

**Bug.** `use "a.niko"` formatted as `use '"a.niko"'` — because
`node.module` keeps the raw text with quotes (`parser.py`
`UseStmt(line_no, text[4:].strip())`) and the formatter rendered
`{node.module!r}`. **Fix:** strip one surrounding quote pair if
present, render `use "<mod>"` (mirrors `ImportStmt` rendering).

### 4. `use` inside a function body (`niko2/typecheck.py`)

`UseStmt` in `visit_stmt`'s `elif` chain was a bare `pass` → now
errors when `len(self.scopes) > 1` with `'use' is only allowed at the
top of a file, not inside a function` — mirroring the adjacent
`import` rule's message (`'import must be at the top of the file'`).

## Deliberate limits (unchanged / not in scope)

- Editor diagnostics with modules parse + typecheck every reachable
  module on each didChange: no mtime cache yet; future work.
- Diagnostics are still line-oriented (AST nodes carry line numbers
  but not columns): hover and go-to-definition resolve to the nearest
  sensible line, not an exact range.

## As built

Verified end-to-end against the working tree by the test/docs worker
(test+docs role; implementation by the sibling worker). Every behavior
below passed as implemented:

- LSP (all via real multi-file LSP sessions in `tests/test_lsp.py`,
  8 new Alpha 23 checks):
  - (a+b) import aliases and `use`d names produce no phantoms.
  - (c) `m.nope` → exactly 1 diagnostic on the attribute's line.
  - (d) missing import → exactly 1 diagnostic on the import line, no
    cascade.
  - (e) an entry type error surfaces.
  - (f) a module-file type error is attributed to that file's URI +
    line.
  - (g) didOpen with fixed unsaved text for the module clears its
    diagnostic (the `source_overrides` path).
  - (h) two no-op entry changes prove the entry's analysis uses the
    in-memory override and never publishes stale module diagnostics
    (the `_diags_owner` staleness fix — caught during this alpha and
    fixed in the implementation).
  - The Alpha 17 section now expects the 1 `cannot find module`
    diagnostic (was `[]`), and the Alpha 22 section expects exactly 1
    `cannot find module` instead of the old phantom `unknown name`
    assertion — both confirmed as behavior improvements, not test
    churn.
- Debugger (`tests/test_dap.py`): new
  `test_no_breakpoint_refire_on_call_line` — breakpoint on a call
  line, step in → 'step' in the callee, continue → program runs to
  `terminated` with no second stop, output correct. The old
  clear-before-stepOut workaround in `test_use_under_debugger` is
  removed: the test keeps the breakpoint armed through step-out and
  asserts reason `'step'`, which is the direct proof the re-fire is
  gone.
- Formatter (`tests/test_formatter.py`): `use "a.niko"` / `use
  'b.niko'` → `use "a.niko"` / `use "b.niko"`, no doubled quotes,
  output re-parses.
- Checker (`tests/test_use_pipeline.py`): `use`-in-function errors at
  the checker level (`TypeErrorNiko`, line 2) and at the CLI level
  (exit ≠ 0, message + caret).
- Full suite green: 26 niko1, 16 niko2 + nikoir round trip, all
  `tests/test_*.py` (21 scripts, exit 0).

One investigation note (from the implementation worker): the staleness
bug above surfaced only in the (h) two-no-op-changes test, where a
module-file-rooted analysis cleared diagnostics owned by the entry's
analysis. The `_diags_owner` per-owner tracking fixed it without
touching the fast path.

*Note: this design doc subsumes `ALPHA23_IMPL_NOTE.md` (the
implementation worker's source note for the docs worker); the note
file has been folded in and removed.*
