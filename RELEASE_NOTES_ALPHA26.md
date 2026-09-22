# Niko 2.0 — Alpha 26: tooling polish batch

Three small, high-value developer-experience wins: REPL `:undo`, an
mtime cache for editor diagnostics, and `pkg:` completions in import
strings. No language changes, no network, no redesigns — all three
landed as planned.

## What changed

**REPL `:undo`** (`niko2/repl.py`). Pops the last accepted chunk and
rebuilds the session from the remaining ones on a fresh VM + globals,
replaying the kept chunks through the normal `exec_chunk` path with
output suppressed. The rebuilt session behaves exactly as if the
undone chunk had never been entered:

- Module init-once is preserved: a kept `import`/`use` initializes its
  module exactly once within the rebuild, and replay output is
  suppressed, so each module's top-level `say` appears exactly once in
  the visible transcript.
- Echo numbering (`__repl_echo_N`) and cumulative line numbers are
  re-derived from the kept chunks — a diagnostic after undo says
  `line 2`, not `line 3`.
- `:undo` on an empty session prints `Nothing to undo.` — a friendly
  message, not an error.
- Limits: no redo; undo rewinds interpreter state only. External side
  effects are replayed, not rewound (a kept chunk that writes a file
  writes it again; a kept `ask` chunk re-prompts during the rebuild).
  Documented in `:help`, the README REPL section, and
  `niko2/KNOWN_LIMITATIONS.md`.

**LSP diagnostics mtime cache** (`niko2/lsp.py`, `_ModuleTreeCache`).
Alpha 23's module-aware `_analyze()` re-parsed every module on every
`didChange`; now each module's parsed tree is cached keyed on
(mtime, size):

- Invalidation rule: a module counts as changed when its (mtime, size)
  differs, when it can't be stat'ed, or when an unsaved open document
  overrides it. A changed module invalidates its own entry **and
  everything downstream** — importers, transitively, using the previous
  analysis's import/use edges. Over-invalidation is safe;
  under-invalidation would publish stale diagnostics. (Downstream
  matters because `use` merges dependency names into scope and the
  alias-attr lint reads dependency exports.)
- Cache hits are fed through the existing `source_overrides`
  mechanism, so the module pipeline needed zero changes. Unsaved-override
  trees are never stored, so a later save can't serve stale in-memory
  content. The fast path (no top-level import/`use`) is untouched.
- Measured on a synthetic 51-file / 1140-line project (medians over 20
  runs after warmup): **29.9 ms → 10.5 ms per analysis (2.8× faster)**,
  with re-parses dropping from 50 to 0 on repeat analyses. Remaining
  warm cost is `check_units` + the alias-attr lint, which still run each
  analysis — the cache deliberately eliminates re-parsing only.

**LSP `pkg:` completions** (`niko2/lsp.py`, `_complete_pkg_import`). When
completing inside an import string starting with `pkg:`, the server now
offers installed package names from the local package cache (honoring
`NIKO_PKG_CACHE`); after `pkg:<name>/` it offers `.niko` file paths
inside that package (nested included, e.g. `sub/util.niko`), resolved
exactly as at compile time (lockfile pin wins, else newest cached).
Plain non-`pkg:` strings are untouched; an empty/unresolvable cache
yields no completions, never an error; the network is never consulted.

## Verification

- `python3 tests/test_repl.py` → 30 checks green, including 9 new undo
  checks (undo after set/to/use/import chunks, undo-empty message,
  multi-undo, line-number re-derivation, echo numbering).
- `python3 tests/test_lsp.py` → all sections green, including new mtime
  cache checks (unchanged tree → zero re-parses; changed leaf
  invalidates downstream importers only; unsaved override wins) and
  `pkg:` completion checks (package names, nested paths, pin-aware
  resolution, empty cache → none, non-pkg strings unaffected).
- Full suite: 40 pytest + 26 Niko 1 + 16 Niko 2 cases + nikoir round
  trip, all green.

## Docs

- `ALPHA26_DESIGN.md` (this sprint's design + "As built", including the
  measured numbers), `NIKO_AI_HANDOFF.md`, `NEXT_STEPS.md` (Alpha 26
  complete), `niko2/KNOWN_LIMITATIONS.md` (REPL: `:undo` exists, still
  no redo), `:help` text.
