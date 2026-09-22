# Niko 2.0 — Handoff for the Next Developer or AI

Status snapshot: **Alpha 7 complete** (language server + VS Code extension + DAP debugger). Last verified: 2026-09-21.
Everything below was confirmed against the code and the passing test suite,
not guessed.

Paste this file (and the project zip) at the start of a new conversation.
Then read `NIKO_AI_DEVELOPER_BRIEF.md` in the repo for the full philosophy.

---

## 1. What Niko is (one paragraph)

Niko is a general-purpose programming language whose programs read like
English instructions. Long-term goal: readable syntax + static safety +
strong tooling + its own execution model + a useful standard library +
multiple backends (VM today, native / WebAssembly later). It is **not** a
toy and **not** meant to stay a Python transpiler.

## 2. Two engines — know which one you're touching

| Engine | Files | What it is |
|---|---|---|
| Niko 1 (compatibility) | `niko.py`, `ide/niko-ide.html` | Translates Niko to Python/JS. Keep working; changes here need both engines in sync. |
| **Niko 2 (the future)** | `niko2/` package | Lexer → Parser → AST → Typechecker → Compiler → Bytecode → VM. No Python source is generated. |

All current development happens in **Niko 2**. Niko 1 stays available as a
compatibility engine.

## 3. How Niko 2 works, module by module

Source flows through these passes, each in `niko2/`:

```
file.niko
  → lexer.py       tokens
  → parser.py      AST (ast.py node types)
  → typecheck.py   static type checking; errors look like: line 3: unknown name "totl". Did you mean "text"?
  → compiler.py    stack bytecode (bytecode.py / ir.py)
  → vm.py          executes the bytecode
  → nikoir.py      save/load of serialized ".nikoir" artifacts (skip re-parse/re-check)
```

Supporting pieces:

- `modules.py` — resolves `use name` / `use "path.niko"`, caches modules, shared VM env
- `stdlib.py` — standard-library organization foundation (VM builtin functions live here/wired in `vm.py`)
- `formatter.py` — code formatter (`niko2 format`)
- `project.py` — `niko.toml` manifest, project discovery (`niko2 init`, `niko2 info`, `niko2 deps`, `niko2 lock`)
- `cli.py` / `__main__.py` — the `python -m niko2` CLI
- `lsp.py` — Language Server Protocol server (`niko2 lsp`): diagnostics,
  completion, hover, go-to-definition, formatting
- `symbols.py` — line-oriented AST symbol walk backing the LSP features
- `jsonrpc.py` — Content-Length framing shared by the LSP and DAP servers
- `debug.py` — debugger core: breakpoints + step in/over/out via the VM's
  `trace_fn` hook (off by default); pauses at one-source-line granularity
- `dap.py` — Debug Adapter Protocol server (`niko2 debug`)
- `editors/vscode/` — VS Code extension: TextMate grammar, language
  client, DAP debug type; packaged as `niko-0.7.0.vsix` (install via
  *Extensions: Install from VSIX…*; not on the Marketplace)

CLI commands today: `run`, `check`, `build`, `disasm`, `init`, `info`,
`format`, `deps`, `lock`, `lsp`, `debug`. (Note: `niko2 run file.nikoir` and
`niko2 disasm file.nikoir` work directly on serialized artifacts.)

### Language surface that works (Niko 2, verified by tests)

`set` (typed `set age: number to 25` and untyped), `say`, `if` / `otherwise if` /
`otherwise`, `repeat N times`, `repeat forever`, `while`, `for each ... in`,
`to NAME with A, B -> type` (recursion works), `give back`, `put`/`remove`/
`add ... to`/`take ... from`, indexed `set` (`set item N of LIST to V`,
`set RECORD["key"] to V`, `set LIST[n] to V`), `ask`/`ask number ... into`,
`numbers A to B` ranges, lists, records, `X[i]` indexing, `N of LIST`,
`record.attr` access, and the operators/comparisons from the Niko 1 brief.
Output conventions: `yes`/`no`/`nothing`, 1-based list positions, `text(n)`
to join numbers.

### Latest improvement (Alpha 6 complete — this snapshot)

Alpha 6 is done. WIP1 checked `list<T>` generics for real (literal
element-type inference, element-by-element annotation enforcement, `put`
checks, element types from `X[i]` / `item_of`, typed `for each` vars,
nested `list<list<T>>` annotations). WIP2 added the **option/result
error model**: `option<T>` (a `T` or `nothing`) and `result<T>`
(`ok(v)` / `error(msg)`) with builtins `ok`, `error`, `is_ok`,
`is_error`, `unwrap`, `unwrap_or`, `error_message`, `try_read_file`,
`try_number` — failure as a value, no exceptions-as-control-flow. WIP3
added **pattern matching**: the `match` statement (the old "reserved for
Niko 2.1" stub shipped early) with `when` arms, optional `otherwise:`,
literal / `ok name` / `error name` / bare-binding / multi-alternative
patterns, first match wins, subject evaluated once, compiled to existing
jump opcodes + `is_ok`/`unwrap` calls (no new VM opcodes). Tests:
`tests/niko2_cases/results.niko` + `match.niko` (+`.out`),
`tests/test_results_match.py` (five checker rejections, six still-legal
cases, `unwrap` runtime messages, hermetic `try_read_file` round trip),
match cases in `tests/test_formatter.py`.

## 4. Tests — how to validate (run these, in order)

```bash
python3 tests/run_niko2_tests.py -q    # Niko 2 end-to-end: parse→check→compile→VM, incl. .nikoir round trip
python3 tests/test_diagnostics.py      # typo-suggestion regression
python3 tests/test_formatter.py        # formatter round trip
python3 tests/test_project_deps.py     # dependency graph
python3 tests/test_generics.py         # list<T> generics rejections
python3 tests/test_results_match.py    # option/result + match rejections, runtime, hermetic file round trip
python3 tests/test_stdlib.py           # stdlib helpers, hermetic file round trip
python3 tests/run_tests.py             # Niko 1 engine, 26 cases (must stay green)
```

Working rules (from the project): preserve line numbers in diagnostics, add
or update tests for every change, run the whole suite, update user docs for
visible syntax, and never claim a feature is complete unless implemented
**and** tested.

## 5. Known gaps (so you don't assume things work)

- The old Niko 1 stdlib gap is closed (WIP12 — all 9 helpers implemented).
- Generics: `list<T>` is fully checked (Alpha 6 WIP1), but builtin *return*
  types (`sorted`, `unique`, …) still return `any`, call arguments aren't
  checked against parameter annotations, and `map<K,V>` has no value-type
  inference yet.
- Error model: `option<T>` / `result<T>` with the `ok`/`error` builtins
  (Alpha 6 WIP2). `match` works as a statement and as an expression
  (`set x to match v:`, Alpha 12); guards and list/record patterns shipped
  in Alpha 11.
- Full detail lives in `niko2/KNOWN_LIMITATIONS.md` — read it before planning work.

## 6. What to do next (priority order)

Alpha 5, Alpha 6, and Alpha 7 are all complete. The language core has
checked `list<T>` generics, an option/result error model, and pattern
matching; the package dependency graph (`niko2 deps`) and lock file
(`niko2 lock`) ship; and the tooling story is real — language server,
VS Code extension, and a DAP debugger, all tested.

1. **Alpha 7 is done**: `niko2 lsp` (LSP: diagnostics, completion, hover, go-to-definition, formatting), `editors/vscode/` (TextMate grammar + language client + DAP debug type, packaged as `niko-0.7.0.vsix`), `niko2 debug` (DAP adapter over `niko2/debug.py`; VM has an off-by-default per-instruction `trace_fn` hook). Tests: `tests/test_lsp.py`, `tests/test_dap.py`, `editors/vscode/test-grammar.js`.
2. **Alpha 8 is done**: WebAssembly backend — `niko2 wasm <file> [-o out.wasm] [--run]`, boxed f64 value model, `tests/test_wasm.py` (differential vs VM). Limits: no file-I/O builtins, no `ask` without a host shim, no closures over enclosing function locals. See `RELEASE_NOTES_ALPHA8.md`.
3. **Alpha 9 is done**: native backend — `niko2 native <file> [-o out] [--run] [--emit-c]`. Compiles checked AST to C (`niko2/backends/native.py`), then to a real executable via the system C compiler using `niko2/backends/niko_runtime.c/.h` (same boxed value model as WASM). Differential `tests/test_native.py` (14 programs vs VM, closure rejection, native-only file-I/O + `ask` tests). Extras over WASM: `ask` (stdin) and file builtins work natively. Limits: no `use` imports, no method-call syntax, no first-class function values, no closures over enclosing function locals, needs `cc`/`gcc`/`clang`. See `RELEASE_NOTES_ALPHA9.md`.
4. **Alpha 10 is done**: closures + first-class functions on ALL backends (VM, WASM, native). Nested `to` captures enclosing locals by reference (shared boxes; write-through `set`); functions are values (aliases, arguments, return values, list/record members); `say f` prints `function "add"`; calling a non-function errors `I can't call 5 as a function.`; bare builtins are not values (`can't use the builtin "length" as a value`, `pi` exempt). Shared analysis in `niko2/closures.py`; VM uses `Cell` boxes; WASM adds `TAG_FUNCTION = 7` with a funcref table + `call_indirect`; native adds `NVal` tag 7. Tests: `tests/niko2_cases/closures.niko` (+`.out`), `tests/test_closures.py` (VM-side + 3-way differential VM/WASM/native, byte-identical), `tests/test_wasm.py` + `tests/test_native.py` run the 8 canonical programs. Full suite green. See `RELEASE_NOTES_ALPHA10.md`.
5. **Alpha 11 is done**: match guards + list/record patterns on ALL backends. `when PATTERN if EXPR:` (guard checked after bindings, may use them; failed guard falls through); `[a, b]`, `[]`, `[first, ...rest]`, `{name: n, age: a}`, nested arbitrarily. New AST nodes `MatchCase`/`MatchList`/`MatchRest`/`MatchRecord`; checker types element bindings from `list<T>` and rejects patterns against statically known wrong types; VM gains tiny `IS_LIST`/`IS_RECORD`/`LIST_SLICE` opcodes with hidden `$patN` slots for nested subjects; WASM mirrors with tag/length/item helpers; native restructures `_gen_match` (bind-then-guard can't live in an `if/else if` chain) with new `nval_list_slice`/`nval_record_has` helpers. Tests: `tests/niko2_cases/match_patterns.niko` (+`.out`), `tests/test_match_patterns.py` (checker rejections + 3-way differential, byte-identical). Full suite green. See `RELEASE_NOTES_ALPHA11.md` and `ALPHA11_DESIGN.md`.
6. **Alpha 12 is done**: match as an expression on ALL backends. `set x to match v:` / `give back match v:` / `say match v:` — the winning arm's final expression is the value. New AST node `MatchExpr`; parser reuses the arm parser via `parse_match_arms`; checker enforces exhaustiveness (`otherwise:` or unguarded catch-all `when x:` as last arm, else `match expression must be exhaustive`) and arm-type agreement (`match arms produce different types`), and requires each arm body to end with an expression. VM: `Compiler.match_expr` with hidden `$matchval` slot; WASM/native mirror with fresh result locals/temps. Formatter, symbols (LSP), and closures analysis handle the new node. Tests: `tests/niko2_cases/match_expr.niko` (+`.out`), `tests/test_match_expr.py` (checker rejections + 3-way differential, byte-identical). Full suite green. See `RELEASE_NOTES_ALPHA12.md` and `ALPHA12_DESIGN.md`.
7. **Alpha 13 is done**: multi-file modules on ALL backends. `import "path/to/file.niko" as alias` (top of file only, `.niko` suffix required, `as alias` required); the alias binds to a record of the module's exports (top-level `set`/`to` names) — qualified access `m.add(2, 3)`, `m.factor`. Each module's top-level code runs exactly once per program (import cache); transitive imports work; import cycles are a compile error (`import cycle: a.niko -> b.niko -> a.niko`) pointing at the closing import line; errors inside a module are reported against the module file with line/col carets; `use` is rejected inside imported modules. `niko2/modules.py` desugars all modules into ONE Program before any backend sees it (per-module wrapper functions `__import$mK` + `$result` slots pre-declared as `nothing`, reusing Alpha 10's closure machinery), so VM, WASM, and native output is byte-identical. Bonus: WASM gained method-call support (`obj.method(...)` via the first-class callee path); `import`/`as` added to LSP KEYWORDS and the VS Code TextMate grammar; formatter + LSP symbols handle `ImportStmt`. Tests: `tests/test_modules.py` (7 programs 3-way differential, byte-identical + 7 error cases). Full suite green. See `RELEASE_NOTES_ALPHA13.md` and `ALPHA13_DESIGN.md`.
8. **Alpha 14 is done**: Niko 2 standard library + module search path, on ALL backends. Four pure-Niko modules bundled under `niko2/stdlib/` — `text` (12 fns), `math` (10), `lists` (9), `records` (7, incl. `record_keys`, a `keys()` that can't be shadowed) — imported as `import "stdlib/text.niko" as text`, byte-identical on VM/WASM/native (WASM/native bundle the source at compile time; no runtime file lookup). No new builtins; deterministic and backend-portable by rule (no randomness/clock/file I/O). `import` now resolves: importing file's dir → `NIKO_PATH` dirs → bundled stdlib (`stdlib/…` paths) → cwd; a local/`NIKO_PATH` `stdlib/` tree shadows the bundled one; `pyproject.toml` ships the `.niko` files as package data. Docs are generated from doc comments: `tests/test_stdlib2.py` executes every `# example:` (`--write-docs` regenerates `STDLIB.md`; the suite fails if it's stale) and runs the whole corpus 3-way differential + twice for determinism. Bugfix found by the stdlib: the VM resolved bare builtin calls globals-first, so `import "stdlib/text.niko" as text` broke `lists.group_by`'s internal `text(...)` call — the VM now emits `CALL_BUILTIN` for a builtin name in direct call position, matching the checker/WASM/native rule (shadowing still works as a value and for `m.attr`). Tests: `tests/test_stdlib2.py` (check + doc coverage + clean top level + executable examples + 3-way differential + determinism + error/`NIKO_PATH` cases); full suite green (26 niko1, 15 niko2 + nikoir round trip, all `test_*.py`). See `RELEASE_NOTES_ALPHA14.md`, `ALPHA14_DESIGN.md`, `STDLIB.md`.
9. **Alpha 15 is done**: WASM codegen bugfix — `skip` inside `for`/`repeat` loops hung node forever (the WASM emitter's `br` back to the loop head never advanced the index; visible both as a bare `skip` and as `skip` inside a `match` arm inside a loop — the bug wasn't match-specific). Root cause found by differential testing: plain `skip` in a `for` loop hung too. Fix: `loop_stack` entries are now `(brk, cont, on_skip)` triples; `for`/`repeat` register an index-increment thunk that `SkipStmt` runs before branching back; `while` registers `None` (re-evaluating the condition is correct there). `stop` verified correct on all backends. `stdlib/lists.niko`'s `flatten` drops its `set nothing_to_add to yes` workaround for the clean `when []: skip` arm; the known-limits entry is removed. Tests: new `tests/test_loop_control.py` — 11 cases (skip/stop in match arms in for/while/repeat, nested loops, guards + skip), each 3-way differential (VM/WASM/native, byte-identical) with timeouts so a regression can't hang the suite. Full suite green. See `RELEASE_NOTES_ALPHA15.md`.
10. **Alpha 16 is done**: package manager — `niko2 get <directory|git-url>` (the ONLY command that touches the network; shallow git clone, `.git` stripped), packages cached at `~/.niko/packages/<name>-<version>/` (override: `NIKO_PKG_CACHE`) with `.niko-source.json` provenance. `import "pkg:<name>/path/file.niko" as alias` — no version in the string; lockfile pin wins, else newest cached, nothing cached → `unknown package "foo" — run 'niko2 get <source>' to install it first`. `niko2 lock` adds the `"packages"` table (`{"version", "source"}` per package) and never silently upgrades; existing lockfile shape untouched. Manifest: canonical `[package]` table (`name`, `version`, `entry` default `main.niko`, optional `description`), Alpha 4-era flat keys still accepted, unknown fields ignored, name `^[A-Za-z][A-Za-z0-9_-]*$`, strict `X.Y.Z` versions. `niko2 init` writes the canonical form. Compile/run/check/wasm/native/debug/lsp resolve packages offline from the cache. No `..` escapes from package dirs; `import "stdlib/text.niko"` unaffected. Deliberate limits: no registry server, no `publish`, no version-range solving, no `niko` command alias (ambiguity with Niko 1's `niko.py` — see `ALPHA16_DESIGN.md` "As built"). Worked example: `examples/packages/` (+`README.txt`). Tests: `tests/test_packages.py`; full suite green. See `RELEASE_NOTES_ALPHA16.md`, `ALPHA16_DESIGN.md`.
11. **Alpha 17 is done**: cross-file LSP go-to-definition. `textDocument/definition` on `name` in `alias.name` (where `alias` is an `import` alias) jumps to the top-level `set`/`to` defining `name` in the module file; on the import's quoted path string it opens the module file itself. Reuses `modules.resolve_import`, so the editor resolves exactly what the compiler resolves — importing file's dir → `NIKO_PATH` → bundled stdlib (`stdlib/…`) → cwd → package cache (`pkg:` via `niko.lock` pin, else newest cached) — including relative `./` imports inside cached packages. Unresolvable imports yield the LSP `null` result, never an error. Cursor on the alias itself still jumps to the `import` line. Limits: line-level (not column) targets, hover doesn't follow imports yet, one hop only. Tests: extended `tests/test_lsp.py` (multi-file fixture: `m.add`/`m.tau` land at the right lines, import string opens the module, `pkg:` + `./`-in-package resolution, missing-module graceful null, alias pin). Full suite green. See `RELEASE_NOTES_ALPHA17.md`.
12. **Alpha 18 is done**: debugger upgrades — multi-file debugging, `ask` under the debugger, and `evaluate`. `niko2/debug.py` now compiles through the module pipeline (`build_module_graph`/`check_units`/`desugar_imports`): per-module wrapper functions (`__import$mK`, nested `__import$mK$name`) keep original line numbers, and each frame's qualname maps back to its source file by prefix, so breakpoints are per-file, stepping crosses files at one-line granularity, and every DAP `stackTrace` frame carries its own `source` (file + line). VM: new `input_fn` hook (default `input`) used by the `INPUT` opcode, and `Frame` carries `qualname` (CALL passes `fn.code.qualname`). `ask` under the debugger: the DAP adapter answers via a reverse `input` request (prompt in arguments) with a bounded wait (30s default, `inputTimeout` launch arg, wakes early on disconnect) — silence/error yields `""` + a warning, so the session can never hang on input; Niko-specific, stock VS Code doesn't answer it. New DAP `evaluate` request: expression typechecked with the paused frame's names defined, run on a fresh VM with an instruction budget (can't re-pause or hang the session); clean failures for unknown names/parse errors. Compile-time errors now end the session with `Niko error: …` + `terminated` instead of hanging the client. Tests: extended `tests/test_dap.py` with a timeout-guarded DAP client (raw-fd reads + select; the old blocking client is untouched) — multi-file fixture (breakpoint inside the module hits with module file/line in the frame, `evaluate` `a + b` → `5`, `stepOut` lands back in the entry file, entry breakpoint still works), `ask` answered via reverse `input`, and a never-answered `input` proving the timeout fallback terminates. Full suite green. See `RELEASE_NOTES_ALPHA18.md`.
13. **Alpha 19 is done**: package registry + version-range solving. `niko2/semver.py`: `parse_range`/`satisfies`/`max_satisfying(versions, range) -> str|None`/`SemverError` — grammar: `*`, `1.2.3`, `1.2` (=`1.2.x`), npm-style `^` (leftmost non-zero specified component pinned: `^0.2.3` -> `>=0.2.3, <0.3.0`), `~` (same minor), `>=`/`>`/`<=`/`<` (operand may be X/X.Y/X.Y.Z), `=`/`==`, comma AND; stdlib-only, no intra-package imports. `niko2/registry.py`: registry protocol (index format spec in module docstring — `{"packages": {name: {version: {url, sha256 (mandatory), description, dependencies}}}}`; `url` relative or absolute), config (`NIKO_REGISTRY` env — index URL, `file://` URL, or local dir path — wins over `~/.niko/config.toml` `[registry] url`), `install_from_registry` (max satisfying version; sha256-verified download, mismatch = hard error + bad file deleted; unsafe tarball paths rejected; transitive `[dependencies]` closure from the same registry, skipping already-satisfied cached versions; dependency cycles terminate), `publish_package` (tarball excludes `.git/`/`__pycache__`/`*.pyc`; atomic index update tmp+rename; overwrite refused without `force`; remote registries refused: "publishing needs auth, which is not supported yet"), `update_package` (re-resolves via lockfile range else `*`; registry packages only), `is_registry_spec`/`split_registry_spec` disambiguation (`<name>`/`<name>@<range>` with valid name + parseable range = registry lookup, everything else falls to dir/git; a same-named bare dir needs `./`). `niko2/packages.py`: manifest gains validated top-level `[dependencies]` (name -> range); lockfile `"packages"` entries gain optional `range` (backwards-compatible `{version, source}` shape kept); new `find_lock_dir`/`write_package_pin`. `niko2/cli.py`: `niko2 get <name>`/`niko2 get <name>@<range>` (install max satisfying, write pin `{version, source: "registry:<spec>", range}`), `niko2 get --update <name>` (argv pre-scan for the `get --update <name>` position because of the pre-existing argparse quirk — `niko2 get --force <src>` was already broken that way), `niko2 publish [--registry <dir|url>] [--force]`, `--registry` flag. Decisions/limits: no auth, no remote publish, no range solving at import time (imports stay version-free; pins in `niko.lock`), transitive deps NOT pinned in lockfile (cache + declared ranges), `get` always picks the highest satisfying version and rewrites the pin to what it installed (announced, never silent). Tests: new `tests/test_registry.py` (hermetic: local-dir registry fixture, sandboxed `NIKO_PKG_CACHE`, `NIKO_REGISTRY` fixture, no network; real `~/.niko` asserted untouched) — semver units, disambiguation, publish layout/guards, get by name/range/pin, lock keeps pin, `--update` (upgrade/range/no-op/errors), sha256 mismatch, registry selection (dir/`file://`/config/env-wins/errors), git-URL `get` unaffected, transitive deps (closure, skip-satisfied, cycle terminates, unsatisfiable error). Full suite green. See `RELEASE_NOTES_ALPHA19.md`, `ALPHA19_DESIGN.md` (with "As built").
Before adding new features, weigh fixing the gaps above — tooling like the
formatter is more useful once the language surface is closer to complete.
