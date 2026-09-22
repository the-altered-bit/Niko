# Alpha 20 design: interactive REPL (`niko2 repl`)

New file: `niko2/repl.py` (~470 lines: `ReplSession` + `main()`).
Modified: `niko2/cli.py` — added `'repl'` to the command choices and the
dispatch (`if a.command=='repl': from .repl import main as repl_main;
return repl_main()`). Niko 1 untouched; the parser untouched.

## Goals

A live session on the VM backend where every chunk is parsed, typechecked
and run with full knowledge of earlier chunks, imports initialize once,
blocks are entered naturally (blank line / dedent ends them), bare
expressions echo their value, and no error — parse, type, runtime,
Ctrl-C, Ctrl-D — can kill or corrupt the session.

## Design decisions

- **Persistence approach.** The session keeps every accepted chunk in
  `src_parts` (the *effective* source: a bare expression the user typed
  is stored as a synthetic `set __repl_echo_N to (<expr>)` line so it
  typechecks like any other statement). Before a new chunk runs, the
  whole accumulated source is re-parsed and re-typechecked — plain
  `check()` when there are no imports, otherwise a REPL-aware replica of
  the module pipeline (`build_module_graph` + per-unit checking with the
  entry unit pre-defining the session's `use`/forward-reference names +
  `desugar_imports`), because the entry is a fake `<repl>` path, not a
  file. Only the **delta** is compiled and executed: the desugared
  program is always `[M pre-declares][M wrappers][M init calls][entry
  stmts…]` (M = imported modules), so the new chunk is exactly the
  trailing entry statements after `entry_count`. One `VM` + one globals
  dict (`env`) live for the whole session; `:reset` drops them.
- **Import init-once.** Wrapper defs / pre-declares / init calls are
  re-emitted only for modules whose path is not yet in
  `initialized_modules`, so re-checking the accumulated source never
  re-runs a module body — a module with top-level `say` prints exactly
  once across chunks, including re-imports under a new alias. Relative
  imports resolve against **cwd** (the entry path is `cwd/'<repl>'`, the
  documented rule). `use "…"` statements in a new chunk go through
  `VMLoader` exactly like `niko2 run`.
- **Block rule.** A submitted line whose stripped form ends in `:` opens
  a block — detected with a quote-parity heuristic (escapes honoured) so
  `when "a:b":` still opens but a trailing colon inside a string doesn't.
  Continuation prompt is `.... `. The block ends on a blank line or a
  dedented line, except `otherwise:` / `when `, which continue the
  enclosing `if`/`match`. An indent stack handles nesting (`when` arms
  dedent one level without closing the `match`); a dedented plain line is
  stashed as the start of the next chunk.
- **Echo rule.** The comment-stripped chunk is tried with `parse_expr`
  first; on success the synthetic `set` is compiled and `fmt(value)`
  printed — `nothing` is never echoed. Anything else runs as a program:
  `say` prints, `set`/`to` are silent.
- **Commands.** `:help`, `:quit`/`:exit` (exit 0), `:reset` (drops VM,
  env, history). Unknown `:foo` gets a plain-English hint. Commands are
  only recognized at the primary prompt.
- **Robustness.** Errors print the same caret diagnostics as scripts
  (cumulative session line numbers); a catch-all keeps tracebacks out of
  the session. Ctrl-C discards the partial buffer and re-prompts;
  Ctrl-D exits 0, submitting an open block first.

## Bug found and fixed (implementation)

`vm.run_module()` replaces `vm._functions` with each compiled module's
function table, so nested functions compiled in an earlier chunk (e.g.
`counter$bump`) became unresolvable in later chunks. Fixed REPL-side by
merging each compiled module's table into a session-cumulative function
table before `run_module` (the VM itself is untouched). Covered by the
cross-chunk closure test (`counter` → `11`, `12`).

## As built

Verified end-to-end against the working tree (implementation worker's
code), not just read — no implementation bugs found by the test/docs
worker; everything below passed as implemented:

- Persistence: `set x to 5` then `say x + 1` → `6`; redefined names
  typecheck against the newest definition.
- Multi-line `to` definition (blank line ends block) then call → `5`.
- Echo: `1 + 2` → `3`, `"hi"` → `hi`, `[1, 2]` → `[1, 2]`; bare `nothing`
  echoes nothing; `say nothing` (a statement) still prints `nothing`.
  `1 + 2 # three` → `3`; `"a # b"` keeps the `#`.
- Type error (`set x to undefined_name`) prints the caret diagnostic and
  the session survives; runtime error (`say s + 1` on text) prints the
  Niko error and the session survives; a rejected chunk never joins the
  history (a later clean `set n` works).
- `:help` / `:quit` (exit 0) / `:reset` (then `say x` → unknown name) /
  unknown `:foo` → plain-English hint.
- `import "stdlib/text.niko" as text` then
  `text.capitalize("hi")` → `Hi`.
- Local module with top-level `say`, imported in two chunks under two
  aliases → body printed exactly once; exports still resolve.
- Cross-chunk closure: `counter` factory defined in one chunk, `c()`
  called in later chunks → `11`, `12` (the `run_module` function-table
  fix).
- `match` with `when`/`otherwise:` across dedents works; dedented plain
  line after an `if` block starts the next chunk.
- EOF on empty input → exit 0; EOF mid-block → the block is submitted,
  runs, then exit 0.

## Documented limits

- Diagnostics show cumulative session line numbers, not per-chunk ones.
- The block-open colon is detected with a quote-parity heuristic, not
  full string parsing.
- Multi-line constructs need increasing indentation; spaces expected.
- No single-chunk undo — `:reset` is the only forget.
- Redefining a function replaces its nested helpers session-wide.
- VM only: WASM/native are compile targets, not REPL targets.

Tests: `tests/test_repl.py` — 20 checks, all driving real `niko2 repl`
subprocesses with scripted stdin in a hermetic throwaway cwd (HOME
sandboxed too), each with a 30s timeout so a regression hangs loudly
instead of blocking the suite.
