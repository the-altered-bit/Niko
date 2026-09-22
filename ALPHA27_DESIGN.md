# Alpha 27 design: `niko2 test` + `assert`

## Problem

Niko 2 had a compiler, VM, two compile targets, a package manager, an
LSP, a debugger, and a REPL — but no way to test Niko code itself. The
repo's own suites are Python harnesses around the CLI. A Niko
programmer's only failure idiom was `if cond: unwrap(error("msg"))`.

## Design

### `assert` as a statement, not a builtin

Two routes were considered:

1. **Builtin** `assert(cond)` / `assert(cond, msg)`: no parser change,
   but a builtin call can't easily carry the *source line* of the
   failure into the error, and Niko builtins are expressions — a bare
   `assert(x)` line would work but reads oddly next to `say`/`ask`.
2. **Statement** `assert <expr> [, <message>]`: mirrors `say`'s slot in
   `parse_stmt`'s `startswith` chain (same `text[6].isspace()` guard so
   identifiers beginning with `assert` still parse as expressions),
   gets a real AST node (`AssertStmt(line, cond, message, source)`),
   checker rules (condition: boolean, message: text), and a line number
   on the failure.

Route 2 won: it reads like the language, the failure message can name
the expression's source text *and* the line, and the typechecker can
reject `assert 5` at check time. Collision check: `assert` appeared
nowhere in `tests/niko2_cases/`, `niko2/stdlib/`, or `examples/`.

### VM codegen

```
<cond> ; JUMP_IF_FALSE -> fail ; JUMP -> end
fail: <message or ""> ; ASSERT "<source-text>"
end:
```

The message compiles only on the failure path (lazy, like Python).
`ASSERT` pops the message and raises
`NikoRuntimeError(f'Line {line}: Assertion failed: "{source}" is not
true[": {msg}"]')` — the same error class as every other runtime
failure, so the test runner needs no assert-specific handling.

### Backends

- **WASM** (`_gen_assert`): mirrors the VM — truthiness check, panic
  with the identical message text (verified byte-for-message against
  the VM). The two-arg form concatenates via the existing `concat3`
  helper.
- **Native**: raises `CompileError("assert is not supported on the
  native backend")` with the assert's line. The C runtime has no
  text-concat helper for building the panic message; rather than add
  runtime surface for one statement, it fails loudly at compile time.
  Documented in `KNOWN_LIMITATIONS.md`; easy to revisit.

### The runner (`niko2/test_runner.py`)

**Discovery**: `niko2 test [dir]` (default `.`), recursive
`*_test.niko` / `test_*.niko`, hidden dirs skipped, single `.niko`
file target runs just that file. No config — convention only.

**Test shape**: top-level `FunctionDef`, no params, name starts with
`test_`. The space form (`to test foo:`) parses as a function literally
named `'test foo'`; `test foo()` is a `ParseError`, so it can never be
invoked — deliberately not collected (test + docstring note).

**Isolation**: per test, compile `file_source + "\n<testname>()\n"`
via `compile_source` (the full module pipeline — `import`/`use` work
exactly like `niko2 run`) and execute on a fresh `VM()` via
`compile_ast` + `run_module`. A bare `test_foo()` line parses as
`ExprStmt` and performs the call. Appending at the end keeps every
original line number (parsing is line-oriented), so diagnostics point
at real file lines — verified: a failure on line 2 reports line 2.
Rejected alternative: compile once and invoke functions through VM
internals — no public API exists for that; the appended-invocation
approach uses only the CLI's public pipeline. Per-test recompile cost
is fine for a dev tool. No module-level caches exist anywhere in the
pipeline, so fresh compile + fresh VM = total isolation (an isolation
test mutates a global in one test and asserts the next sees it fresh).

**Failures**: any exception during a test's run is a failure,
formatted with `cli._format_error`/`_format_parse_error` (identical to
`niko2 run`). A file that fails to compile is exactly one `FAIL <file>
(does not compile)` entry — a broad `except Exception` guard means the
runner never crashes on a bad file.

**Reporting**: `PASS name (file)` / `FAIL name (file)` lines, plain
text, no color codes, deterministic order; each FAIL followed by the
diagnostic indented two spaces; summary `N passed, M failed`; exit 0
iff all pass; no tests found → message + exit 0. `say` prints straight
through (no capture-assertion API exists that capturing would serve).

**VM only**: the runner is a dev tool, the VM is the reference
semantics; WASM/native are compile targets — per-test toolchain
spin-up would be slow and can't easily invoke individual test
functions.

### Deliberately not built

Fixtures, mocks, coverage — documented as future work. The `assert`
two-arg message must be a text *expression* (any text-valued expr, not
just a literal).

## As built

- `assert` landed on VM + WASM with identical messages; native
  refuses cleanly. Formatter round-trips `assert`.
- The runner's 3 assert-integration tests pass against the real
  `assert` implementation (no stubs).
- `examples/testing/` worked example verified on the real CLI
  (`3 passed, 0 failed`, exit 0); breaking the code under test shows
  the failure format.
- Full suite green: 26 Niko 1, 16 Niko 2 + nikoir round trip, 71 pytest.
- Gotcha captured for test authors: Niko 2 functions return `nothing`
  unless they `give back` a value (in the example README).
