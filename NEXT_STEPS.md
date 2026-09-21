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
  Marketplace yet), `niko2 debug` (DAP adapter; VM `trace_fn` hook is
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
  Limits: no `use` imports, no method calls, no first-class functions,
  no closures over enclosing function locals, needs `cc`/`gcc`/`clang`.
- **Alpha 10 is complete**: closures + first-class functions on ALL backends
  (VM, WASM, native) — nested `to` captures enclosing locals by reference,
  functions are values, byte-identical output across backends. Tests:
  `tests/niko2_cases/closures.niko`, `tests/test_closures.py` (3-way
  differential), `tests/test_wasm.py`, `tests/test_native.py`. See
  `RELEASE_NOTES_ALPHA10.md`.

## Standing cautions

- `parse_stmt` in `niko2/parser.py` is a flat `startswith` chain — after any
  edit near it, view surrounding lines and re-run the suite; a dropped
  branch fails confusingly, not obviously.
- Never claim a feature complete until it is implemented **and** tested.
- Keep the Niko 1 engines (`niko.py`, `niko-ide.html`) behaving the same;
  their 26-case suite must stay green.
