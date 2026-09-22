# Niko 2.0 — What to do next

For the next developer or AI picking up the project. Ordered by priority.
Each item is sized to be one focused session. Follow the working rules in
`NIKO_AI_HANDOFF.md` section 4 (tests + docs for every change).

## 1. ~~Finish Alpha 5: source columns and carets in errors~~ — DONE (WIP11)

`niko2/diagnostics.py` added; `ParseError`/`TypeErrorNiko`/`CompileError`
all carry line/column/caret; `tests/test_diagnostics.py` covers it.
Alpha 5 is complete.

## 2. ~~Close the Niko 1 stdlib gap~~ — DONE (WIP12)

All 9 missing helpers implemented with Niko 1's semantics (`starts_with`,
`ends_with`, `count_of`, `pick`, `write_file`, `append_file`, `read_file`,
`read_lines`, `file_exists`), wired into both builtin registries,
registered in the typechecker, grouped in `niko2/stdlib.py` (new `files`
group). **Files decision: real files** (working-directory-relative),
matching Niko 1's desktop engine — documented in
`niko2/KNOWN_LIMITATIONS.md`. Tests: `tests/niko2_cases/stdlib_text.niko`
(+`.out`) and `tests/test_stdlib.py`.

## 3. ~~Alpha 6: make generics real~~ — DONE (Alpha 6 WIP1)

`list<T>` is now really checked: literal element-type inference
(`[1, 2, 3]` → `list<number>`, nested too), element-by-element annotation
enforcement on literals, `put` value checking, element types from `X[i]` /
`item_of(i, X)`, typed `for each` loop variables, nested `list<list<T>>`
annotations parse. Heterogeneous literals stay legal (`list<any>`).
Tests: extended `tests/niko2_cases/generics.niko` (+`.out`),
`tests/test_generics.py`.

## 4. ~~Alpha 6: option/result error model~~ — DONE (Alpha 6 WIP2)

Failure as a value: `option<T>` (a `T` or `nothing`; a plain `T` is a
valid `option<T>`) and `result<T>` (`ok(v)` / `error(msg)`, error side
always text — Niko errors are plain English). New builtins: `ok`, `error`,
`is_ok`, `is_error`, `unwrap`, `unwrap_or`, `error_message`,
`try_read_file`, `try_number`. The checker infers `ok(42)` as
`result<number>`, types `unwrap` as `T`, rejects bare values assigned to
`result<T>` variables. The raising builtins are unchanged; `try_*` is the
opt-in value path. Tests: `tests/niko2_cases/results.niko` (+`.out`),
`tests/test_results_match.py`.

## 5. ~~Alpha 6: pattern matching~~ — DONE (Alpha 6 WIP3)

The `match` statement (the old "reserved for Niko 2.1" stub shipped
early): `when` arms + optional `otherwise:`, literal / `ok name` /
`error name` / bare-binding / comma-alternative patterns, first match
wins, subject evaluated once. Checker types bindings and rejects
`ok`/`error` patterns against known non-results; the compiler desugars
to existing jumps + `is_ok`/`unwrap` calls (no new VM opcodes). Tests:
`tests/niko2_cases/match.niko` (+`.out`), `tests/test_results_match.py`,
match cases in `tests/test_formatter.py`.

**Alpha 6 is complete.** The roadmap's `niko2 deps` (dependency graph) and
`niko2 lock` (lock file) already shipped earlier.

## 6. After that (roadmap order)

- **Alpha 7 is complete**: `niko2 lsp` (LSP server: diagnostics, completion,
  hover, go-to-definition, formatting), `editors/vscode/` (TextMate grammar,
  language client, DAP debug type; packaged `niko-0.7.0.vsix`, not on the
  Marketplace yet — see Alpha 25), `niko2 debug` (DAP adapter; VM `trace_fn` hook is
  off-by-default). Tests: `tests/test_lsp.py`, `tests/test_dap.py`,
  `editors/vscode/test-grammar.js`. Limits: single-file debugging, no `ask`
  under the debugger, line-oriented hover/definition.
- **Alpha 8 is complete**: WebAssembly backend (`niko2 wasm`). Tests:
  `tests/test_wasm.py`. Limits: no file-I/O builtins, `ask` needs a host
  shim, no closures over enclosing function locals.
- **Alpha 9 is complete**: native backend (`niko2 native`) — AST → C →
  executable via the system C compiler (`niko2/backends/niko_runtime.c`,
  `native.py`). Tests: `tests/test_native.py` (differential vs VM).
  Native extras over WASM: `ask` and the file-I/O builtins work.
  Limits (at the time; `use` landed in Alpha 22): no `use` imports, no
  method calls, no first-class functions, no closures over enclosing
  function locals, needs `cc`/`gcc`/`clang`.
- **Alpha 10 is complete**: closures + first-class functions on ALL backends
  (VM, WASM, native) — nested `to` captures enclosing locals by reference,
  functions are values, byte-identical output across backends. Tests:
  `tests/niko2_cases/closures.niko`, `tests/test_closures.py` (3-way
  differential), `tests/test_wasm.py`, `tests/test_native.py`. See
  `RELEASE_NOTES_ALPHA10.md`.
- **Alpha 11 is complete**: match guards (`when PATTERN if EXPR:`, checked
  after bindings, failed guard falls through) + list patterns (`[a, b]`,
  `[]`, `[first, ...rest]`) + record patterns (`{name: n, age: a}`), nested
  arbitrarily, on ALL backends (VM, WASM, native) with byte-identical
  output. Tests: `tests/niko2_cases/match_patterns.niko`,
  `tests/test_match_patterns.py` (checker rejections + 3-way differential).
  See `RELEASE_NOTES_ALPHA11.md` and `ALPHA11_DESIGN.md`.
- **Alpha 12 is complete**: match as an expression on ALL backends.
  `set x to match v:` / `give back match v:` / `say match v:` — the winning
  arm's final expression is the value. Checker-enforced exhaustiveness
  (`otherwise:` or unguarded catch-all) and arm-type agreement; each arm
  body must end with an expression. Tests:
  `tests/niko2_cases/match_expr.niko`, `tests/test_match_expr.py`
  (checker rejections + 3-way differential, byte-identical).
  See `RELEASE_NOTES_ALPHA12.md` and `ALPHA12_DESIGN.md`.
- **Alpha 13 is complete**: multi-file modules on ALL backends.
  `import "path/to/file.niko" as alias` (top of file only, `.niko` suffix
  required, `as alias` required); the alias binds to a record of the
  module's exports (top-level `set`/`to` names) — qualified access
  `m.add(2, 3)`, `m.factor`. Each module's top-level code runs exactly
  once per program (import cache); transitive imports work; import cycles
  are a compile error (`import cycle: a.niko -> b.niko -> a.niko`); errors
  inside a module are reported against the module file. `niko2/modules.py`
  desugars all modules into ONE Program before any backend sees it
  (per-module wrapper functions `__import$mK` + result slots
  `__import$mK$result`, reusing Alpha 10's closure machinery), so VM,
  WASM, and native output is byte-identical. `use` is rejected inside
  imported modules. Bonus: WASM gained method-call support
  (`obj.method(...)` via the first-class callee path), and
  `import`/`as` were added to LSP keywords + the VS Code grammar.
  Tests: `tests/test_modules.py` (7 programs 3-way differential +
  7 error cases). See `RELEASE_NOTES_ALPHA13.md` and `ALPHA13_DESIGN.md`.
- **Alpha 14 is complete**: Niko 2 standard library (pure-Niko `text`,
  `math`, `lists`, `records` under `niko2/stdlib/`, `import
  "stdlib/text.niko" as text`, byte-identical on VM/WASM/native) +
  documented module search path (file dir → `NIKO_PATH` → bundled
  stdlib → cwd) + docs generated from doc comments (`STDLIB.md`, kept
  fresh by `tests/test_stdlib2.py`) + VM `CALL_BUILTIN` fix (builtin
  name in call position always means the builtin, matching
  checker/WASM/native). Full suite green. Package manager is done in
  Alpha 16; a registry server stays future work. See
  `RELEASE_NOTES_ALPHA14.md`, `ALPHA14_DESIGN.md`,
  `STDLIB.md`.
- **Alpha 15 is complete**: WASM codegen bugfix — `skip` inside
  `for`/`repeat` loops used to hang node forever (the `br` back to the loop
  head never advanced the index; visible as `skip` inside a `match` arm
  inside a loop). `skip` now emits the index increment before branching
  back, matching the VM; `stop` verified correct. `stdlib/lists.niko`'s
  `flatten` uses the clean `when []: skip` arm (workaround removed);
  known-limits entry removed. New `tests/test_loop_control.py`: 11 cases
  3-way differential (VM/WASM/native byte-identical) with timeouts. Full
  suite green. See `RELEASE_NOTES_ALPHA15.md`.
- **Alpha 16 is complete**: package manager — `niko2 get
  <directory|git-url>` (the only command that touches the network;
  shallow git clone, `.git` stripped) installs into
  `~/.niko/packages/<name>-<version>/` (`NIKO_PKG_CACHE` overrides), with
  `.niko-source.json` provenance. `import "pkg:<name>/path/file.niko" as
  alias` — no version in the string; resolution is lockfile pin, else
  newest cached, else a friendly `unknown package` error; compile/run /
  check / wasm / native / debug / lsp resolve packages offline from the
  cache. `niko2 lock` adds the `"packages"` table (`{"version",
  "source"}` per package) and never silently upgrades; `niko2 deps` shows
  `pkg:` imports. Manifest: canonical `[package]` table (`name`,
  `version`, `entry` default `main.niko`, optional `description`);
  Alpha 4-era flat keys still accepted, unknown fields ignored; name
  `^[A-Za-z][A-Za-z0-9_-]*$`, strict `X.Y.Z` versions; `niko2 init`
  writes the canonical form. No `..` escapes from package dirs;
  `import "stdlib/text.niko"` unaffected. Limits: no registry server,
  no `publish`, no version-range solving, no `niko` command alias (kept
  unambiguous with Niko 1's `niko.py`). Worked example in
  `examples/packages/` (+`README.txt`). Tests: `tests/test_packages.py`;
  full suite green. See `RELEASE_NOTES_ALPHA16.md`, `ALPHA16_DESIGN.md`.
- **Alpha 17 is complete**: cross-file LSP go-to-definition.
  `textDocument/definition` on `name` in `alias.name` (where `alias` is
  an `import` alias) jumps to the top-level `set`/`to` defining `name` in
  the module file; on the import's quoted path string it opens the module
  file itself. Reuses `niko2/modules.py`'s `resolve_import`, so the editor
  resolves exactly what the compiler resolves — importing file's dir →
  `NIKO_PATH` → bundled stdlib (`stdlib/…`) → cwd → package cache (`pkg:`
  via `niko.lock` pin, else newest cached) — including relative `./`
  imports inside cached packages. Unresolvable imports yield the LSP
  `null` result, never an error. Cursor on the alias itself still jumps to
  the `import` line. Limits: line-level (not column) targets, hover
  doesn't follow imports yet, one hop only. Tests: extended
  `tests/test_lsp.py` (real LSP sessions over a multi-file fixture:
  `m.add`/`m.tau` land at the right lines, the import string opens the
  module file, `pkg:` via a lockfile pin, `./` import inside the package,
  missing-module graceful null, alias pin). Full suite green. See
  `RELEASE_NOTES_ALPHA17.md`.
- **Alpha 20 is complete**: interactive REPL — `niko2 repl` (new
  `niko2/repl.py`: `ReplSession` + `main()`; `niko2/cli.py` gains the
  `repl` command). Prompts `niko> ` / `.... `; a line ending in `:`
  opens a block (quote-parity heuristic for colons in strings), blank
  line or dedented line ends it, `otherwise:`/`when ` continue the
  enclosing `if`/`match`, indent stack handles nesting. Persistence:
  accepted chunks accumulate in `src_parts` (bare expressions stored as
  synthetic `set __repl_echo_N to (<expr>)`); each chunk re-parses +
  re-typechecks the whole accumulated source (plain `check()`, or the
  module pipeline against a fake `<repl>` entry when imports are used)
  and only the delta is compiled + run on one session VM. Imports
  initialize once per session (module top-level `say` prints once across
  chunks incl. re-imports); relative imports resolve against cwd;
  `use "…"` via `VMLoader` (replaced by the shared module pipeline in
  Alpha 22). Echo: bare expressions print `fmt(value)`,
  `nothing` never echoes. Implementation bug found & fixed: cumulative
  function table merged into each compiled module before `run_module`
  (VM untouched) so earlier chunks' nested functions stay resolvable.
  Robustness: caret diagnostics (cumulative session line numbers),
  Ctrl-C re-prompts, Ctrl-D exits 0 (open block at EOF submitted first),
  `:help`/`:quit`/`:exit`/`:reset`, unknown `:foo` gets a hint. Limits:
  VM only, no single-chunk undo, spaces for indent. Tests: new
  `tests/test_repl.py` (20 checks, scripted stdin into real `niko2 repl`
  subprocesses, hermetic). Full suite green. See
  `RELEASE_NOTES_ALPHA20.md` / `ALPHA20_DESIGN.md`.
- **Alpha 21 is complete**: tooling polish batch. **Parser**: `to name:
  -> type:` and `to name with x: -> type:` now parse — the trailing colon
  is the block opener, never part of the identifier (only headers that
  previously produced uncallable functions changed, so no behavior
  regressions possible); typed params and mid-string colons untouched;
  line numbers preserved. **Debugger**: DAP `setBreakpoints` honors the
  `condition` field — evaluated in the paused frame's context via the
  Alpha 18 `evaluate` machinery, stops only when truthy; bad conditions
  emit `Niko warning: breakpoint condition "…" failed: <reason>` and the
  breakpoint stops as if unconditional (stop-anyway fallback); the
  session never dies or hangs. **LSP**: `textDocument/hover` follows
  imports like go-to-definition does (signature + `#` doc comment from
  the module file; `pkg:` imports included; unresolvable → null hover).
  **Cut**: `use` under the debugger — pre-loading used files would
  produce a wrong session (ambiguous frames, un-attributable
  breakpoints); the real fix is routing `use` through the module
  pipeline with `__use$K` wrapper prefixes (reusing Alpha 18's
  attribution scheme), a future sprint — **done in Alpha 22**). Tests: new
  `tests/test_function_header.py`,
  `tests/niko2_cases/function_colon.niko` (+`.out`), 4 new conditional
  breakpoint DAP cases, new cross-file hover LSP assertions. Full suite
  green. See `RELEASE_NOTES_ALPHA21.md`.
- **Alpha 22 is complete**: `use` routed through the module pipeline.
  `use "file.niko"` now goes through the same graph → check →
  desugar pipeline as `import` (`niko2/modules.py`): use-units (key
  `uK`, disjoint from import keys `mK`) desugar to `to __use$uK:`
  wrappers returning `{exports}` records; each used file's top-level
  code runs exactly once (diamond-safe, transitive); entry `use`s
  become hoisted `set nm to __use$uK$result.nm` bindings — later `use`
  wins on conflicts, the entry's own `set`s/`to`s win over used names.
  Works byte-identically on VM/WASM/native (the old WASM/native
  `CompileError("...doesn't support 'use' yet")` is gone). New errors:
  `use cycle: a -> b -> a` (was silently recursive),
  `cannot find module` for missing files,
  `'import' is not supported inside used modules`,
  `'use' is not supported inside imported modules` (kept). Builtin-name
  uses (`use "math"`) stay no-ops everywhere. `ModuleLoader`/`VMLoader`
  deleted; `cli.py`, `debug.py` (`__use$uK` frame attribution),
  `lsp.py` (go-to-definition + hover follow `use`), and `repl.py`
  (shared pipeline instead of `VMLoader`) updated. Known gaps: LSP
  editor diagnostics still flag used names as unknown; `niko2 format`
  renders `use "a.niko"` with doubled quotes; `use` inside a function
  body stays silently ignored. Tests: new `tests/test_use_pipeline.py`
  (13 checks: 3-way differential + error cases + attribution), new
  `test_use_under_debugger` DAP case, new LSP definition/hover
  assertions, new REPL `use-in-repl` case. Full suite green. See
  `RELEASE_NOTES_ALPHA22.md` / `ALPHA22_DESIGN.md`.
- **Alpha 23 is complete**: module-aware editor diagnostics. `niko2
  lsp` now analyzes the whole module graph: import aliases and
  `use`-merged names no longer get phantom `unknown name` squiggles,
  `m.nope` is flagged on the attribute's line, unresolvable imports
  yield exactly one diagnostic on the import line, and type errors
  attribute to the right file/line; unsaved module buffers override
  disk. Single-file documents keep the old zero-disk-IO fast path.
  Also fixed: the debugger's same-line breakpoint re-fire (breakpoint
  on a call line now fires once, loops still re-fire per iteration),
  `niko2 format`'s doubled quotes on `use`, and `use` inside a
  function is now an error (was silently ignored). Tests: 8 new
  `tests/test_lsp.py` Alpha 23 checks, new
  `test_no_breakpoint_refire_on_call_line` DAP case, formatter `use`
  quote case, `use`-in-function checker + CLI cases. Full suite green.
  See `RELEASE_NOTES_ALPHA23.md` / `ALPHA23_DESIGN.md`.
- **Alpha 24 is complete**: registry hardening. HTTP(S) registries work
  end to end (`NIKO_REGISTRY=http://…/index.json`; `file://` never touches
  the network); fetch timeout 30 → 10s with plain-English errors for HTTP
  status / DNS failure / connection refused / timeout; no-partial-install
  ordering (cache touched only after download + sha256 + manifest checks).
  `niko2 lock` pins the full transitive closure (`{version, source,
  range}` per package, `range` = the parent's requested range; re-locking
  never silently upgrades; conflicting live requirements are a hard error
  naming both parents/ranges and the fix; cycles terminate). After every
  registry `get`, the CLI backfills lockfile-pinned versions missing from
  the cache, exactly (`✓ installed locked dependencies: b 1.1.0`,
  strictly additive), so a fresh machine reproduces the author's tree.
  `get --update` re-resolves and re-pins the updated package's subtree.
  Known limit (pre-existing, now tested + documented): a `pkg:` import
  *inside* a cached package resolves to the newest *cached* version, not
  the pin — direct project imports always honor the pin. Auth and remote
  publish remain future work. Tests: 11 new `tests/test_registry.py`
  Alpha 24 sections (hermetic HTTP fixture on 127.0.0.1); worked example
  `examples/registry-http/` (publish → serve → get → lock → run).
  Full suite green. See `RELEASE_NOTES_ALPHA24.md` / `ALPHA24_DESIGN.md`.
- **Alpha 25 is complete**: VS Code extension refresh + Marketplace
  publish prep. The extension was packaged once as `niko-0.7.0.vsix` in
  Alpha 7 and had gone stale; it's now `niko-0.25.0.vsix` (version marks
  it current through Alpha 25; publisher `niko-lang`, id `niko`
  unchanged). Grammar audited against the parser/checker: added
  `put`/`remove`/`add`/`take`, `from`/`into`, the long-form comparison
  operators (`is bigger than` … `is in`, longest-first), single-quoted
  strings; the builtin list is verified programmatically against
  `BUILTIN_NAMES`. `test-grammar.js` gained 17 cases + a builtin-sync
  self-check. `package.json` description sells the real feature set;
  added the `inputTimeout` launch attribute and `activationEvents`
  (modern `vsce` requires it). README fixed (the stale "`use` imports
  are not loaded under the debugger" line, accurate feature list, the
  `ask`-under-VS-Code caveat, new vsix filename). New
  `editors/vscode/PUBLISH.md` runbook — publishing itself needs the
  user's publisher account + token, so it stays a user action. The old
  `niko-0.7.0.vsix` is removed. Verified: grammar tests green; scripted
  LSP session (exact `python3 -m niko2 lsp` command) over a multi-file
  program — diagnostics, cross-file hover, go-to-definition, formatting;
  scripted DAP session (exact `python3 -m niko2 debug` command) —
  conditional breakpoint, cross-file frames, evaluate, `ask` yielding
  `""` after the unanswered reverse `input` timeout; one clean headless
  VS Code run (later headless runs SIGSEGV'd inside the Electron binary
  itself — a container issue). Full suite green. See
  `RELEASE_NOTES_ALPHA25.md`.
- **Alpha 19 is complete**: package registry + version-range solving.
  `niko2/semver.py` (range grammar: `*`, exact, partials, npm-style
  `^`/`~`, comparators, comma AND; `max_satisfying` solver,
  `SemverError`), `niko2/registry.py` (registry protocol: local-dir
  registry `<dir>/index.json` + `tarballs/`, or a remote index URL;
  index format spec in the module docstring; sha256 mandatory,
  mismatch = hard error with bad download deleted; `NIKO_REGISTRY`
  env wins over `~/.niko/config.toml`). CLI: `niko2 get
  <name>[@<range>]` (newest satisfying version + lockfile pin
  `{version, source: "registry:<spec>", range}`), `niko2 get --update
  <name>` (re-resolve via the pin's range; upgrades + re-pins, else
  friendly no-op; both flag positions work via an argv pre-scan for
  the pre-existing argparse quirk), `niko2 publish [--registry
  <dir>] [--force]` (tarball excludes `.git/`/`__pycache__`/`*.pyc`,
  atomic index update; remote publish refused: "publishing needs auth
  — not supported yet"). Disambiguation: bare `<name>`/`<name>@<range>`
  = registry lookup (a same-named local dir needs `./`; unparseable
  range falls through to dir/git handling). Manifest `[dependencies]`
  table (name → range) installs the full transitive closure from the
  same registry, skipping already-satisfied cached versions;
  transitive deps are NOT pinned in the lockfile. No solving at
  import time — imports stay version-free. Tests: new
  `tests/test_registry.py` (hermetic: local-dir registry fixture,
  sandboxed cache, no network; real `~/.niko` asserted untouched).
  Full suite green. See `RELEASE_NOTES_ALPHA19.md` /
  `ALPHA19_DESIGN.md`.
- **Alpha 18 is complete**: debugger upgrades — multi-file debugging,
  `ask` under the debugger, and `evaluate`. The debugger compiles through
  the module pipeline, so breakpoints/stepping/stack traces follow
  execution into imported modules (each frame's qualname maps it back to
  its file; every `stackTrace` frame carries its own `source`). The VM's
  `INPUT` opcode reads through a new `input_fn` hook; the DAP adapter
  answers `ask` with a reverse `input` request to the client, bounded
  (30s default, `inputTimeout` launch arg, wakes on disconnect) so a
  silent client can never hang the session. New DAP `evaluate` request:
  simple expressions against a paused frame's locals, run on a fresh VM
  with an instruction budget. Tests: extended `tests/test_dap.py`
  (timeout-guarded DAP client; multi-file breakpoint/step/evaluate
  fixture, `ask` answered via reverse `input`, never-answered `input`
  proving the timeout fallback). Full suite green. See
  `RELEASE_NOTES_ALPHA18.md`.

## Standing cautions

- `parse_stmt` in `niko2/parser.py` is a flat `startswith` chain — after any
  edit near it, view surrounding lines and re-run the suite; a dropped
  branch fails confusingly, not obviously.
- Never claim a feature complete until it is implemented **and** tested.
- Keep the Niko 1 engines (`niko.py`, `niko-ide.html`) behaving the same;
  their 26-case suite must stay green.
