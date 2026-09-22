# Alpha 22 design: `use` routed through the module pipeline

Goal: make `use "file.niko"` go through the same graph → check →
desugar pipeline as `import`, instead of the old side path
(`ModuleLoader`/`VMLoader` executing used files outside the pipeline,
VM-only). One change fixes four things at once: WASM/native support
for `use`, debugger attribution inside used files, LSP cross-file
navigation for used names, and the nested-function orphaning bug for
functions defined in used files (the same class of bug the Alpha 20
REPL fixed session-side).

## Design (as implemented)

**Graph.** `build_module_graph` now records two edge kinds.
`import` edges make import-units (key `mK`); `use` edges make use-units
(key `uK`). The two key namespaces are disjoint, so a file reached both
ways keeps two distinct units. `ModuleUnit.kind` is `'import'`,
`'use'`, or `'entry'`; the kind of a non-entry unit is the edge that
first reached it. A file reached by *both* an import and a use edge
gets whichever unit was created first; the second edge reuses the
existing unit (per-path dedup is unchanged).

**Cycle detection.** `visit()` tracks `(path, edge_kind)` on the DFS
stack. Closing a loop raises `ImportErrorNiko` naming the loop. The
label is `'use cycle'` only when *every* edge in the loop is a `use`
edge; a mixed loop is reported as an `'import cycle'`. Previously a
`use` cycle silently recursed (the old `VMLoader.load` had no guard at
all).

**Mutual exclusion.** A unit reached via `import` may not contain
`use` statements ("'use' is not supported inside imported modules --
use 'import "..." as ...' instead", the old Alpha 13 rule kept), and
symmetrically a unit reached via `use` may not contain `import`
statements ("'import' is not supported inside used modules -- import it
from the entry file instead"). The old code raised `unsupported
statement ImportStmt` for the latter; the new message names the rule.

**Resolution.** `resolve_use` mirrors the old `VMLoader.resolve`
precedence: builtin-name check *first* (`use "math"` etc. return
`None` — a no-op on every backend, since the checker/compiler already
know those names), then raw path, then raw + `.niko`, through
importing-file dir → `NIKO_PATH` dirs → bundled stdlib for
`stdlib/`-prefixed paths → cwd. No `pkg:` for `use` (that's import's
syntax). Missing files raise `ImportErrorNiko('cannot find module
"…"'`, line=the `use` line, path=the importing file) — previously a
`NikoRuntimeError` at load time.

**Typechecking.** `check_units` is unchanged in shape: each unit is
checked standalone, dependencies first. A `use` edge pre-defines the
used file's exports as `any` in the importing unit (an `import` edge
binds the alias to `map`); builtin-name uses need nothing. Errors are
re-tagged with the failing unit's path, so caret diagnostics render
against the right file.

**Desugar.** Every non-entry unit becomes `to __use$uK:` (use) or `to
__import$mK:` (import), each returning a record of its top-level
`set`/`to` exports (`{name: <value>, …}`). Bodies keep their original
line numbers. Differences for `use`:

- Inside a used file's wrapper, each `use "…"` statement is replaced
  by `set <nm> to __use$uJ$result.<nm>` bindings for every name the
  target exports (transitive uses flatten into the wrapper's scope),
  so a used file sees its own `use`s unqualified. Builtin-name uses
  inside are dropped (no-op).
- Each wrapper is *called* exactly once, in dependency order, into a
  `__use$uK$result` global — so a transitively-used file's top-level
  code runs exactly once, and diamond `use`s share one execution.
- The entry's own `use` statements become hoisted `set <nm> to
  __use$uK$result.<nm>` bindings emitted *before* the entry body
  (used files run first). Emission order follows the entry's `use`
  statement order, so a **later `use` wins** on name conflicts (its
  binding overwrites the earlier one). The entry body follows, so the
  **entry's own `set`/`to` wins** over every used name.

**Consumers.** `cli.py`: `run` no longer walks `UseStmt`s with a
loader — `compile_source` routes any tree containing `use` through
`prepare_program`, and the result is one desugared Program executed
directly. The `wasm`/`native` commands get this for free through the
same `compile_source`, which is why the old `CompileError("...doesn't
support 'use' yet")` can no longer fire for top-level `use`.
`debug.py`: `_module_files` maps `__use$uK` prefixes to used-file
paths, so breakpoints/stepping/frames attribute correctly.
`lsp.py`: go-to-definition and hover follow `use` (later `use` wins;
entry's own definitions win; builtin-name uses resolve to nothing;
transitive uses followed; `seen` guards cycles). `repl.py`: dropped
`VMLoader` for the shared pipeline — the REPL's accumulated source
goes through the same replica of the module pipeline it already used
for imports. `ModuleLoader` and `VMLoader` are deleted.

## Deliberate limits (unchanged / not in scope)

- `use` inside a function body keeps its old VM behavior (silently
  ignored) — the pipeline only handles top-level `use`.
- `niko2 format` still renders `use "a.niko"` as `use '"a.niko"'`
  (pre-existing doubled-quote quirk, untouched).
- LSP editor *diagnostics* still flag used names as unknown: the
  live checker runs plain `check()` without the pipeline's
  pre-definitions (same pre-existing pattern as `import` aliases).
  Go-to-definition and hover work regardless.
- The debugger's same-line breakpoint re-fire after step-in/step-out
  (a breakpoint on a call line fires again on the call's STORE when
  execution returns to the line) is pre-existing behavior, verified
  identical on single-file programs; not use-specific.

## As built

Verified end-to-end against the working tree by the test/docs worker
(test+docs role; implementation by the sibling worker). No
implementation bugs found — every behavior below passed as
implemented:

- 3-way differential (VM/WASM/native byte-identical): basic `use`,
  transitive `a→b→c`, diamond (top-level `say` exactly once),
  conflicts (later `use` wins; entry's own `set` wins), a nested
  closure defined in a used file called from the entry (the old
  orphaning class of bug), builtin `use "math"` on all three
  backends. (`tests/test_use_pipeline.py`)
- `use cycle: a.niko -> b.niko -> a.niko` and the self-cycle name the
  loop with a caret on the closing edge; missing file, `import`-in-used,
  and `use`-in-imported all error with carets at the right file/line.
- A checker error inside a used file renders against the used
  file/line (`Niko error in …/used_bad.niko: line 2, column 5` with
  caret), not the entry.
- Debugger: breakpoint in a used file verifies + hits with the used
  file/line; step-in lands in the used function; step-out returns to
  the entry; closure locals (`x`, `offset`) inspectable; used-file
  breakpoint fires on a later call. (`tests/test_dap.py`)
- LSP: definition on a used name → the used file's `set`/`to`; on the
  `use` path string → the used file; entry's own `set` wins;
  missing use → null; hover shows signature + doc comment.
  (`tests/test_lsp.py`)
- REPL: `use` in a chunk makes names visible in later chunks;
  top-level `say` runs exactly once across re-`use`s.
  (`tests/test_repl.py`)
- Full suite green: 26 niko1, 16 niko2 + nikoir round trip, all
  `test_*.py` via pytest.

One investigation note: an early manual DAP probe appeared to hang on
step-into a used function and then "restart" the program. Root cause
was two pre-existing debugger behaviors, both reproduced on
single-file programs and unrelated to this alpha: (1) my probe's own
read loop deadlocked; (2) after step-in, `continue` re-fires the
*call line's* breakpoint when the call returns (the post-call `STORE`
still carries the call's line number). The test uses step-out and
clears the entry breakpoint first, and documents the quirk.
