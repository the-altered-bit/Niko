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
  (Alpha 6 WIP2). `match` patterns don't yet include guards, list/record
  patterns, and `match` is statement-only, not an expression (WIP3).
- Full detail lives in `niko2/KNOWN_LIMITATIONS.md` — read it before planning work.

## 6. What to do next (priority order)

Alpha 5, Alpha 6, and Alpha 7 are all complete. The language core has
checked `list<T>` generics, an option/result error model, and pattern
matching; the package dependency graph (`niko2 deps`) and lock file
(`niko2 lock`) ship; and the tooling story is real — language server,
VS Code extension, and a DAP debugger, all tested.

1. **Alpha 7 is done**: `niko2 lsp` (LSP: diagnostics, completion, hover, go-to-definition, formatting), `editors/vscode/` (TextMate grammar + language client + DAP debug type, packaged as `niko-0.7.0.vsix`), `niko2 debug` (DAP adapter over `niko2/debug.py`; VM has an off-by-default per-instruction `trace_fn` hook). Tests: `tests/test_lsp.py`, `tests/test_dap.py`, `editors/vscode/test-grammar.js`.
2. **Then**: WebAssembly backend (Alpha 8), native backend (Alpha 9).

Before adding new features, weigh fixing the gaps above — tooling like the
formatter is more useful once the language surface is closer to complete.
