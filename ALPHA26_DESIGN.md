# Alpha 26 Design: tooling polish batch

Three items, each sized to land without architectural changes. The
standing rule applies: if any item had needed a redesign, it would have
been cut cleanly (like Alpha 21 cut `use`-under-debugger). None did.

## 1. REPL `:undo`

**Design.** Snapshot-free rebuild. The session stores `(user_text,
effective)` chunk pairs. `ReplSession.undo()` pops the last pair and,
if any chunks remain, calls `reset()` (fresh VM, env, cumulative
function table, `initialized_modules`, `echo_seq`) and replays the kept
user chunks through the identical `exec_chunk` path, with stdout
suppressed via `contextlib.redirect_stdout`. Because replay goes
through the normal path, all existing delta-slicing, module-pipeline,
and cumulative-function-table logic is reused — no architectural change.

**Why rebuild, not snapshot.** VM state (heap, globals, function
tables) has no serialization boundary; rebuilding from the kept chunks
is the only honest way to "forget" the last chunk. Cost is proportional
to session length — fine for an interactive REPL.

**Module init-once.** Replay runs the original chunk order, so each kept
`import`/`use` initializes its module exactly once within the rebuild,
and suppressed replay output keeps each module's top-level `say`
appearing exactly once in the visible transcript. The session behaves
as if the undone chunk had never been entered.

**Echo/line numbers.** Both re-derived from the kept chunks
(`echo_seq` restarts at 0; cumulative line numbers count only kept
chunks), so post-undo diagnostics reference the right lines.

**Limits (documented).** No redo. Undo rewinds interpreter state only:
external side effects are replayed, not rewound (a kept file-writing
chunk writes again; a kept `ask` chunk re-prompts during rebuild, prompt
suppressed, reading from stdin). A chunk that failed to replay (only
possible if a module file changed under the session) is counted and
reported as a warning, not a session-killer.

## 2. LSP diagnostics mtime cache

**Design.** `_ModuleTreeCache` maps resolved module path → `(mtime_ns,
size, parsed tree)`. Cache hits are injected through the existing
`source_overrides` mechanism, so `build_module_graph` needed zero
changes; a miss parses from disk exactly as before.

**Invalidation rule.** A module counts as changed when its (mtime, size)
differs, when it can't be stat'ed, or when an unsaved open document
overrides it (always counts as changed; override trees are never
stored, so a later save can't serve stale in-memory content). A changed
module invalidates its own entry **and everything downstream** —
importers, transitively, using the previous analysis's import/use edges.
Rationale: `use` merges dependency names into the importer's scope and
the alias-attr lint reads dependency exports, so importer diagnostics
depend on dependencies. Over-invalidation is safe; under-invalidation
would publish stale diagnostics.

**Scope.** Only the module path engages the cache; the fast path
(no top-level import/`use`) is byte-for-byte untouched. The cache is
in-memory per LSP process (restart = cold start).

## 3. LSP `pkg:` completions

**Design.** `_complete_pkg_import`, hooked at the top of `_complete`,
matches the cursor's line prefix with a line-oriented regex
(`\bimport\s+(['"])pkg:([^'"]*)$` — deliberately not AST-based, matching
how the rest of `_complete` is structured).

- `import "pkg:|` / `pkg:par|` → installed package names from the local
  cache (`$NIKO_PKG_CACHE` honored via `packages.default_cache_root()`),
  completion kind 9 (Module), filtered by typed prefix.
- `import "pkg:<name>/|` → `.niko` files inside the package as relative
  paths (nested included), kind 17 (File). Package resolution mirrors
  compile time: nearest `niko.lock` pin wins, else newest cached.
- Plain (non-`pkg:`) strings return `None` → normal completions
  untouched. Empty/unresolvable cache → `[]`, never an error. Listing +
  `resolve_package` are offline by construction — the network is never
  consulted.

**Limits.** No `textEdit` ranges (the editor filters labels); `pkg:` is
not offered inside `use` (no such syntax). Plain path completion
remains out of scope.

## As built

- All three items landed as designed; no cuts.
- **Measured** (synthetic 51-file / 1140-line project, medians over 20
  runs after warmup): before (no cache) **29.9 ms/analysis with 50
  module re-parses** → after (warm cache) **10.5 ms/analysis with 0
  re-parses** — **2.8× faster** on repeat analyses. Cold first analysis
  still does the 50 parses. Remaining warm cost is `check_units` + the
  alias-attr lint, which still run every analysis — the cache
  deliberately eliminates re-parsing only.
- Test observability: `Server.last_parsed` records paths re-parsed in
  the last *successful* analysis (used by the cache tests; not protocol).
- Tests: 9 new `undo-*` checks in `tests/test_repl.py` (30/30 green);
  mtime-cache and `pkg:` completion sections in `tests/test_lsp.py`
  (all green, incl. in-process `_analyze` + `_ModuleTreeCache` cache
  assertions and end-to-end stdio completion checks with a sandboxed
  `NIKO_PKG_CACHE`).
