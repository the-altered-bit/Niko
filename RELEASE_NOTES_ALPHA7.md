# Niko 2.0 Alpha 7 — release notes

Alpha 7 is the **tooling** release: a language server, a VS Code
extension, and a debug adapter. No language changes — every `.niko`
program that ran under Alpha 6 runs identically. Everything below is
implemented and covered by tests.

## Language server — `niko2 lsp`

A Language Server Protocol server over stdio (`niko2/lsp.py`), sharing a
tiny JSON-RPC framing module with the debugger (`niko2/jsonrpc.py`):

- **Diagnostics as you type** — parse errors and type errors published
  via `textDocument/publishDiagnostics`, reusing the Alpha 5
  line/column/caret diagnostics, so editors get the same messages and
  positions as `niko2 check`.
- **Completion** — keywords, every builtin (the list is imported from
  the checker's new module-level `BUILTIN_NAMES`, so it can't drift),
  and names defined in the file.
- **Hover** — builtin docs, plus inferred types for your own names
  (`set total: number to 0` → `total (var): number`).
- **Go-to-definition** — jumps to where a variable or function was
  defined.
- **Format document** — runs the Alpha 5 formatter over the whole file.

Hover and go-to-definition are line-oriented: AST nodes carry line
numbers but not columns, so results resolve to the nearest sensible
line. The symbol walk lives in `niko2/symbols.py` and is shared by all
three features.

Tests: `tests/test_lsp.py` spawns the real server and speaks LSP —
initialize, didOpen (one diagnostic on the right line), completion,
hover (builtin + user name), definition, formatting, didChange (errors
clear), shutdown.

## VS Code extension — `editors/vscode/`

- **Syntax highlighting**: a TextMate grammar (`syntaxes/niko.tmLanguage.json`)
  covering comments, strings, numbers, keywords, operators (`is`, `and`,
  `or`, …), constants (`yes`/`no`/`nothing`), builtins, function
  definitions, and type annotations. Types only highlight in annotation
  position (`: number`, `list<number>`) — a variable *named* `result`
  stays plain, and `number("42")` still highlights as a builtin call.
- **Language client** (`client/extension.js`): launches `niko2 lsp` via
  `vscode-languageclient`; the Python is configurable as
  `niko.pythonPath`.
- **Debugging**: contributes a `niko` debug type that launches
  `niko2 debug`; F5 on a `.niko` file just works.
- **Packaged**: `editors/vscode/niko-0.7.0.vsix` (629 KB) — install with
  *Extensions: Install from VSIX…*. Not yet published to the Marketplace.
- **Grammar tests**: `editors/vscode/test-grammar.js` tokenizes sample
  lines with the real TextMate engine and asserts scopes
  (`node test-grammar.js` after a throwaway `npm install`).

## Debug adapter — `niko2 debug`

A Debug Adapter Protocol server over stdio (`niko2/dap.py`) on top of a
small debugger core (`niko2/debug.py`):

- The VM gained a per-instruction hook (`vm.trace_fn`, off by default —
  zero overhead when not debugging). The debugger pauses on breakpoints
  and implements step-over/in/out at **one-source-line granularity**: a
  line compiling to several instructions pauses once, not per
  instruction.
- Supported: `launch` (with `stopOnEntry`), `setBreakpoints` (verified),
  `continue`, `next`, `stepIn`, `stepOut`, `threads`, `stackTrace`,
  `scopes`, `variables` (locals, with one level of list/record
  expansion), `disconnect`. The program's `say` output arrives as
  `output` events; `terminated` ends the session.
- Tests: `tests/test_dap.py` speaks real DAP — breakpoint hit three
  times, stack/variables inspection (`total` is `1` at the first hit),
  step-over landing on the loop header, output events, clean
  termination. A direct `Debugger` check also verified step-in lands
  inside the called function and step-out returns.

## Limits (honest, Alpha 7)

- Debugging runs **single-file programs** (`use` imports aren't loaded).
- `ask` for input isn't supported while debugging — the program would
  read from the debug protocol stream.
- Hover/go-to-definition are line-oriented (no column info in the AST).
- The VS Code extension isn't on the Marketplace yet; install the
  `.vsix` manually.

## Verification

- `tests/run_niko2_tests.py`: 12 niko2 cases, 0 failures
- `tests/run_tests.py`: 26 niko1 cases, 0 failures (Niko 1 untouched
  and behavior-identical)
- `tests/test_diagnostics.py`, `test_stdlib.py`, `test_generics.py`,
  `test_results_match.py`, `test_formatter.py`, `test_project_deps.py`,
  `test_lsp.py`, `test_dap.py`: all pass
