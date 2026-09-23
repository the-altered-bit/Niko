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
- **Alpha 26 is complete**: tooling polish batch. **REPL `:undo`**
  (`niko2/repl.py`): pops the last accepted chunk and rebuilds the
  session from the kept chunks on a fresh VM/env — module init-once
  preserved (module bodies never re-run visibly), echo numbering and
  cumulative line numbers re-derived; empty session prints
  `Nothing to undo.`. Limits: no redo; external side effects are
  replayed, not rewound. **LSP diagnostics mtime cache** (`niko2/lsp.py`,
  `_ModuleTreeCache`): parsed trees cached keyed on (mtime, size); a
  changed module invalidates itself and all transitive downstream
  importers; unsaved overrides always count as changed and are never
  cached; fast path untouched. Measured on a synthetic 51-file project:
  29.9 → 10.5 ms/analysis (2.8×), 50 → 0 re-parses on repeats. **LSP
  `pkg:` completions**: inside `import "pkg:…` offers installed package
  names from the local cache (honors `NIKO_PKG_CACHE`); after
  `pkg:<name>/` offers `.niko` paths inside the package (lockfile pin
  wins, else newest cached). Non-`pkg:` strings untouched; empty cache
  → no completions, never an error; no network. Tests: 9 new `undo-*`
  checks (`tests/test_repl.py`, 30/30 green); mtime-cache + `pkg:`
  sections in `tests/test_lsp.py`. Full suite green. See
  `RELEASE_NOTES_ALPHA26.md` / `ALPHA26_DESIGN.md`.
- **Alpha 27 is complete**: `niko2 test` test runner + `assert`
  statement. `assert <expr>` / `assert <expr>, "message"` — boolean
  condition + text message, typechecked; failure raises a Niko error
  naming the expression source and line (`Line 5: Assertion failed:
  "2 * 2 == 5" is not true: maths broke`), message evaluated lazily.
  VM + WASM (identical messages); native refuses with a clean compile
  error. `niko2 test [dir]` discovers `*_test.niko` / `test_*.niko`
  recursively (no config); tests are top-level `to test_<name>:` with
  no params; each test compiles `file + "\ntest_name()"` through the
  module pipeline and runs on a fresh VM (total isolation;
  `import`/`use` work like `niko2 run`). Plain greppable
  `PASS`/`FAIL` lines, `N passed, M failed`, exit 0 iff all pass.
  Deliberate limits: no fixtures/mocks/coverage; VM-only runner.
  Tests: `tests/test_assert.py`, `tests/test_test_runner.py` (22);
  worked example `examples/testing/`. Full suite green. See
  `RELEASE_NOTES_ALPHA27.md` / `ALPHA27_DESIGN.md`.
- **Alpha 28 is complete**: dogfood sprint — `niko-ssg`, a static site
  generator written entirely in Niko (`examples/ssg/`). No new syntax,
  no new builtins, no network. `ssg.niko` reads `pages.txt`, converts
  each doc with `markdown.niko` (pure Niko, no imports; subset:
  `#`–`###` headings with trailing space, space-joined paragraphs,
  fenced code with optional language → `<pre><code
  class="language-x">` (unclosed fence runs to EOF), inline code,
  bold/italic incl. nesting, `[label](url)` links, `- ` lists,
  `---` → `<hr>`; tables/blockquotes/ordered lists render as literal
  escaped paragraphs — deliberate pragmatic cut, spec'd in
  `examples/ssg/README.md`), applies `layout.html`
  (`{{title}}`/`{{nav}}`/`{{content}}`), writes `site/<slug>.html` +
  `site/index.html`, copies `style.css`. `pages.niko` holds
  slug/nav/index helpers reusing `stdlib/text.niko`'s `slugify`
  (first real stdlib use from user code). `build.sh` writes
  `pages.txt` by globbing `RELEASE_NOTES_ALPHA*.md` and runs the
  generator from the repo root (26 pages: STDLIB.md, NIKO_AI_BRIEF.md,
  24 release-notes files; ~39s). `site/` + `pages.txt` are build
  output — recommendation: gitignore, don't commit (`site/` not yet in
  `.gitignore`). First in-repo suite written in Niko itself:
  `markdown_test.niko` (26) + `pages_test.niko` (4) →
  `niko2 test examples/ssg/` gives 30 passed, 0 failed. Three language
  bugs found by dogfooding, all fixed (~30 lines total, line numbers
  preserved): (1) `niko2/parser.py` `parse_if` — sequential `if`s were
  swallowed as one if/elif chain (second body silently never ran;
  also broke `if` blocks ending in a nested `if`) — continuation loop
  now only accepts same-indent `otherwise if` / `otherwise:`; (2)
  `niko2/modules.py` `check_units` — forward references failed inside
  imported modules (only the entry pre-defined its own top-level
  names) — every unit now pre-defines its own top-level names
  (mutual recursion verified at runtime); (3) `niko2/closures.py` —
  nested closure reading a builtin-shadowed name bound by an enclosing
  function got the builtin (broke `import "stdlib/text.niko" as text`
  inside a module) — capture analysis walks up for an enclosing
  binding first, only truly unbound names skip capture; Alpha 10's
  pinned rule preserved and pinned by a test. Known limits (in
  `niko2/KNOWN_LIMITATIONS.md` under "Alpha 28 dogfood notes"):
  `and`/`or` do not short-circuit — both sides always evaluated on all
  backends, a real semantic divergence from Niko 1 (guard idioms raise;
  the converter uses nested `if`s; fixing needs jump-based codegen in
  VM+WASM+native — a future sprint); runtime errors inside imported
  modules report the entry file's path. Tests: new
  `tests/test_dogfood.py` (12 cases, 3-way VM/WASM/native
  differential). Full suite green. See `RELEASE_NOTES_ALPHA28.md` /
  `ALPHA28_DESIGN.md`.
- **Alpha 29 is complete**: browser playground — Niko 2 in the browser,
  zero install (`examples/playground/`). No new syntax, no new builtins.
  `index.html` loads Pyodide (pinned v0.29.1, the only network request);
  `playground.js` unpacks the embedded `niko2.zip` into Pyodide's
  filesystem and calls the new `niko2/playground.py` adapter — one pure
  function `compile_source_to_wasm(source)` (source string in, WASM
  bytes out; parse → module pipeline if imports/uses → WASM backend;
  no disk IO, no network; the four diagnostic exceptions keep
  human-readable `str(e)`). `niko2_bundle.js` is generated by
  `build.sh` (base64 of `niko2/**/*.py` + `niko2/stdlib/*.niko` plus
  six example programs embedded as text so the page works from
  `file://`); it is committed as the shippable artifact. The JS host
  ports the `wasm_host.cjs` contract (ten `niko` imports; numbers
  formatted into the 4 KiB scratch at `0x0`; `ask` answers into the
  input buffer at `0x800`); a guest panic never crashes the page.
  Porting the host corrected two stale claims in
  `niko2/backends/WASM_DESIGN.md` (`niko_pow` row added; multi-file
  `import`/`use` work on WASM since Alpha 22). Browser subset:
  file builtins fail at compile time with a clean Niko error, `ask` →
  `prompt()`, `sleep` capped 2 s, no `pkg:` imports, infinite loop
  hangs the tab (documented). Editor is a plain `<textarea>`
  (deliberate v1 cut). Tests: `tests/test_playground.py` (7 cases);
  core loop verified headlessly (fibonacci → 12,921 WASM bytes → node
  + host shim prints `fib(20) = 6765`). `e2e.mjs` (headless Chromium
  over CDP, exit 2 = skip when the Pyodide CDN is unreachable) could
  NOT run on the build machine — the CDN is unreachable from it; one
  networked run of `cd examples/playground && node e2e.mjs` is still
  owed. Full suite green. See `RELEASE_NOTES_ALPHA29.md` /
  `ALPHA29_DESIGN.md`.
- **Alpha 30 is complete**: short-circuit `and`/`or` with Niko 1's
  operand-returning semantics, on all three backends. No new syntax,
  no new builtins. The rule: `a and b` → `a` if `a` is falsy else
  `b`; `a or b` → `a` if `a` is truthy else `b`; the right side never
  evaluates when the left side decides; falsy = `no`, `nothing`, `0`,
  `0.0`, `""`, `[]`, `{}` (everything else truthy) — identical to
  Niko 1, which transpiles `and`/`or` straight to Python. Typechecker:
  any operand types accepted (`'{op} requires booleans'` gone),
  result `BOOLEAN` iff both operands boolean else `ANY`. VM: new
  `JUMP_IF_FALSE_OR_POP` / `JUMP_IF_TRUE_OR_POP` opcodes (left value
  stays when it decides, popped otherwise; truthiness via the VM's
  existing `bool()`). WASM: new `_gen_short_circuit` (fresh result
  local, `call truthy`, `br_if $end`, right side into the same local
  only when reached). Native: C temp + branch per the `MatchExpr`
  temp pattern. Two bugs fixed along the way: `niko2/ir.py`
  constant-pool dedup used Python `==` so `True`/`1`/`1.0` collided —
  operand-returning semantics made `say yes and 1` print `yes` (known
  since Alpha 8, invisible until now); now dedupes on exact-type
  match only. Native `WhileStmt` evaluated its condition once before
  the loop — a statement-emitting `and`/`or` condition captured a
  stale temp; now evaluated inside the loop (`while (1) { <cond>; if
  (!truthy) break; }`). The SSG guard idiom `if i < n and item (i +
  1) of s is "*":` works without the nested-`if` workaround. Tests:
  new `tests/test_shortcircuit.py` (3-way VM/WASM/native
  differential: truth tables, dedup cases, skipped-side-never-evaluates
  incl. `1/0`, chained/mixed precedence, `if`/`while`+`stop`/`skip`/
  `say`/call-arg/match-guard contexts, the SSG idiom both forms,
  error attribution, typechecker rule); `tests/cases/logic.niko` +
  `.out` extended with Niko 1's own value-semantics and never-call
  cases (output captured from `niko.py` itself). Kept boundaries:
  `yes * 2` still diverges (native number-only ops vs VM Python
  semantics — pre-existing), runtime errors carry no line numbers in
  `niko2 run` output. `examples/playground/niko2_bundle.js` rebuilt
  from the Alpha 30 tree. Full suite green. See
  `RELEASE_NOTES_ALPHA30.md` / `ALPHA30_DESIGN.md`.
- **Alpha 31 is complete**: `json` stdlib module (`niko2/stdlib/json.niko`,
  pure Niko, no new syntax, no new builtins, no backend changes) —
  `json.parse(text) -> any` (objects → records with last-key-wins,
  arrays → lists, `true`/`false`/`null` → `yes`/`no`/`nothing`) and
  `json.stringify(value) -> text` (compact, keys in insertion order),
  plus 22 `_`-prefixed helpers, all doc-commented (24 doc examples).
  Parse errors raise `json parse error at character N: <msg>` via
  `unwrap(error(...))`, native on all backends. Documented
  limitations: `\uXXXX` above U+007F rejected (use raw UTF-8 on
  VM/native); numbers with magnitude ≥ 1e15 rejected (`number out of
  range` — VM keeps ints exact, WASM/native use f64);
  integer-valued floats render `1.0` (VM) vs `1` (WASM/native).
  Two Alpha 30-era native bugs worked around in pure Niko (no backend
  changes): native `otherwise if` + short-circuit `and`/`or`
  returning `nothing` (branches split into nested `if`s) and native
  hoisting call args past short-circuit guards (`_char_at` is total,
  returns `""` past the end). New WASM backend bug found by the
  tests and documented (not fixed): `while yes:` + `item_of` on
  multibyte strings miscompiles (host `string index out of range`),
  so `json.parse` of raw-multibyte JSON strings fails on WASM while
  VM/native succeed — `tests/test_json.py` asserts the WASM failure
  explicitly ("flip when fixed"). Tests: `'json'` added to `MODULES`
  in `tests/test_stdlib2.py` (24 doc examples 3-way + determinism,
  `STDLIB.md` regenerated); new `tests/test_json.py` (14 malformed
  inputs asserting exact error positions ×3 backends, `\u` escapes,
  ≥1e15 rejection, per-backend `1.0` rendering, round-trip battery,
  50-deep nesting, key order, stringify-of-function errors,
  determinism). Full suite green. See `RELEASE_NOTES_ALPHA31.md` /
  `ALPHA31_DESIGN.md`.
- **Alpha 32 is complete**: LSP rename refactoring —
  `textDocument/prepareRename` + `textDocument/rename` in `niko2/lsp.py`
  (advertised `renameProvider: {prepareProvider: true}`), the last big
  missing editor operation. Locals, parameters, loop variables,
  `ask … into` names and match-pattern bindings rename every in-scope
  reference (nested closures included); shadowing honored (inner renames
  only inner-bound refs). Top-level `set`/`to` renames extend cross-file:
  every `import "x.niko" as alias` file gets its `alias.name` occurrences
  renamed (own alias spelling kept), every `use "x.niko"` file gets
  bare-name refs renamed; importer discovery = open documents first
  (unsaved buffers included) then a bounded read-only walk (≤500 `.niko`
  files, hidden/build dirs skipped) of the project trees; bundled stdlib
  + package cache are in-file only. Never renamed: builtins, keywords,
  string contents, comments, import/use path strings, record keys and
  fields. Import-alias rename is in-file only (module file untouched);
  bare `use`-provided names renamed from the defining file only.
  New-name validation via LSP `InvalidParams` (legal name, not keyword,
  not builtin per `BUILTIN_NAMES`, no scope collision, no capture clash);
  `prepareRename` → range or null, never a silent no-op; unsaved buffers
  work. Mechanically reuses the existing scope machinery
  (`symbols.find_symbol` identity per occurrence line for shadowing,
  `modules.resolve_import`/`_search_use_tree` for cross-file truth —
  same functions go-to-definition uses). Known approximation: match-pattern
  bindings share `symbols.py`'s first-wins scope recording, so renaming a
  pattern-bound name with an earlier same-scope binding also renames the
  earlier binding's refs — documented, not fixed (would alter
  hover/definition). Deliberately out: extract-function, find-references,
  rename preview, beyond-project search. Tests: new
  `tests/test_lsp_rename.py` (28 scripted stdio sessions asserting exact
  edit ranges/full text). `editors/vscode/README.md` documents rename.
  Full suite green. See `RELEASE_NOTES_ALPHA32.md` / `ALPHA32_DESIGN.md`.
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
- **Alpha 33 is complete**: WASM multibyte string indexing fix — the
  bug Alpha 31's `json` testing found. Root cause in
  `niko2/backends/wasm.py` `_b_item_of`: the text branch overwrote the
  byte-length local with the char count from `utf8_len`, then passed it
  as the byte-length argument to `utf8_byte_offset`, so `item_of` on the
  last character(s) of multibyte text returned `""` instead of the
  character; a `while yes:` scan-to-terminator loop (what `json.niko`'s
  parser does) then never matched its terminator and the host panicked
  with `string index out of range`. Fix (3 lines, WASM only): the byte
  length keeps its own local `blen`, passed to both `utf8_byte_offset`
  calls; the other six call sites audited, all already correct. No
  host-contract change. Tests: new `tests/test_wasm_unicode.py` (20
  cases, 3-way differential); `tests/test_json.py` WASM unicode
  round-trip flipped to success. Docs: `KNOWN_LIMITATIONS.md` Alpha 31
  entry marked fixed-in-Alpha-33, `json.niko` header updated,
  `STDLIB.md` regenerated. Full suite green. See
  `RELEASE_NOTES_ALPHA33.md` / `ALPHA33_DESIGN.md`.
- **Alpha 34 is complete**: `niko2 fmt` — the editor formatter on the
  command line. No refactor was needed: the formatter already lived in
  `niko2/formatter.py` (`format_program`) with the LSP
  `textDocument/formatting` handler as a thin wrapper, so the new CLI
  (`fmt_source_text` + `cmd_fmt` in `niko2/cli.py`) runs the identical
  pipeline — CLI and editor cannot disagree by construction, proven by
  differential tests. `niko2 fmt [files...]` rewrites in place
  (reporting `formatted <file>`); `--check` exits 1 with `would
  reformat <file>` instead of writing (0 when clean); no files reads
  stdin and writes stdout. Parse errors report the caret diagnostic and
  leave the file untouched; missing files are plain-English errors.
  `niko2 format` (stdout, single file) kept for backwards compatibility.
  Argparse note: the shared `file` positional is `nargs='?'`, so
  `main()` splits fmt's multi-file list out of argv before parsing
  (same hoist pattern as `--update`). Documented decision: CRLF
  normalizes to LF (what the LSP handler already did). Tests: new
  `tests/test_fmt.py` (248 cases — LSP/shared and CLI-core/shared
  differentials + idempotency over all 79 parseable .niko files under
  tests/, examples/, niko2/stdlib/, plus CLI behavior tests). README
  gained an Alpha 34 section. Full suite green. See
  `RELEASE_NOTES_ALPHA34.md` / `ALPHA34_DESIGN.md`.
- **Alpha 35 is complete**: `niko2 migrate` — the Niko 1 → Niko 2
  migration advisor (`niko2/migrate.py`). Two-phase analysis: heuristic
  line scan of 40 Niko 1 idioms (Niko-1-faithful lexing, strings masked)
  plus Niko 2's own parser + typechecker with first-diagnostic mapping;
  a clean check drops heuristic errors as false positives (except
  `use-python` and `join-arity`, which the checker can't see). Findings
  are (line, severity, code, message, fix-or-None) with a guide-section
  pointer into the new `MIGRATION_GUIDE.md` difference catalog.
  `migrate --fix` applies only semantics-preserving rewrites via a
  bounded fixpoint with idempotency guards; exit 1 on errors, 0 on
  warnings/notes only, 2 on usage/IO errors. The differential audit also
  documented three new divergences in `niko2/KNOWN_LIMITATIONS.md`
  (silent `use <python-lib>`, function-global writes, one unverified
  truthiness question) and fixed four tool bugs along the way. Tests:
  `tests/migrate_cases/` (12 fixtures + pinned `.expected.json` + 4
  `.fixed.niko`) and `tests/test_migrate.py` (pinned findings, fix
  round-trip + idempotency, CLI exit codes, zero errors over
  `tests/niko2_cases/` + `niko2/stdlib/`, Niko 1 spot checks). README
  gained an Alpha 35 section. Full suite green. See
  `RELEASE_NOTES_ALPHA35.md` / `ALPHA35_DESIGN.md`.
- **Alpha 36 is complete**: `niko2 fuzz` — the differential fuzzer
  (`niko2/fuzz.py`, new). Seeded grammar-aware generator + differential
  runner (VM/WASM/native subprocesses, per-backend timeout, exact stdout
  bytes) + greedy line-deletion minimizer + `--corpus` re-run mode +
  expected-divergence allowlist; `--seed/--cases/--backend/--timeout/
  --native-sample/--keep-passing/--no-minimize` flags; exit 0/1/2.
  Termination by construction (pinned structurally by `tests/test_fuzz.py`,
  8 tests: protected while-counters, iterated for-each lists, stop rule,
  give-back type model, tiny fib/fact literals, no ask in loops/functions,
  valid try_number inputs only). Final campaign: 5,000 cases VM vs WASM
  (seeds 101–105) — 5,000/5,000 pass, 0 failures; 200-case native
  characterization found nothing beyond the one known bug. Seven real
  backend bugs documented in `niko2/KNOWN_LIMITATIONS.md` with minimal
  repros — native `otherwise if` miscompile (invalid C + silent
  wrong-branch variant), three-way `ask`-at-EOF divergence (incl. a
  compiled-backend hang), `try_number` error-message divergence,
  native `ask number` prompt skip, **`stop` leaking the VM's loop
  iterator (genuine VM control-flow bug, flagged prominently)**,
  bool-vs-number comparison divergence — all left for backend sprints.
  Three generator bugs fixed along the way. One-line drive-by: WASM
  `error("x")` rendering fixed to match VM. `fuzz_failures/`/`fuzz_work/`/
  `fuzz_corpus/` gitignored (local scratch). README gained an Alpha 36
  section. Full suite green (401 pytest). See
  `RELEASE_NOTES_ALPHA36.md` / `ALPHA36_DESIGN.md`.
