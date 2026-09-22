# Niko 2.0 — Alpha 27: `niko2 test` + `assert`

Niko 2 had a full toolchain but no way to test Niko code itself. Alpha 27
closes that gap with a test runner and one new statement.

## What changed

**`assert` statement** (new syntax — the only syntax added this sprint):

```niko
assert 1 + 1 == 2
assert text.length(name) > 0, "name must not be empty"
```

- The condition must be boolean, the message (optional) must be text —
  both checked by the typechecker with line numbers.
- On failure it raises a Niko error naming the expression's source text
  and the line: `Line 5: Assertion failed: "2 * 2 == 5" is not true:
  maths broke`. The message expression is evaluated lazily, only on
  the failure path (like Python's `assert`).
- Works on the VM and WASM backends (WASM panics with the same
  message). The native backend refuses it with a clean compile error
  (`assert is not supported on the native backend`) — fail loudly,
  never miscompile. The formatter round-trips it.
- `assert` was chosen as a statement (not a builtin) so it reads like
  the rest of the language (`say`, `ask`) and so the failure can carry
  the source line. A grep confirmed no existing code used `assert` as
  an identifier, so nothing breaks.

**`niko2 test [dir]`** (new command):

```console
$ niko2 test
PASS test_add (math_test.niko)
PASS test_mul (math_test.niko)
FAIL test_add_zero (math_test.niko)
  Niko error in math_test.niko: Line 4: Assertion failed: "math.add(0, 5) == 5" is not true.
1 passed, 2 failed
```

- **Discovery**: recursively finds `*_test.niko` and `test_*.niko`
  (default: current directory; a single `.niko` file runs just that
  file). No config file. Hidden directories are skipped.
- **Test model**: every top-level `to test_<name>:` with no parameters
  is a test. (`to test <name>:` — the space form — parses as a function
  literally named `'test <name>'`, which no call syntax can invoke, so
  it is dead code and is not collected.)
- **Isolation**: each test compiles `file source + "\ntest_name()"`
  through the normal module pipeline and runs it on a brand-new VM —
  fresh globals, fresh module, every time. One test's `set` can't leak
  into another. `import`/`use` of the code under test work exactly
  like `niko2 run`.
- **Failures**: a raised Niko error or a failed `assert`. A test file
  that doesn't compile is one reported failure, never a crash.
- **Reporting**: plain greppable `PASS`/`FAIL` lines, failures show the
  error with file:line (same format as `niko2 run`), summary
  `N passed, M failed`, exit code 0 iff all pass. No tests found →
  a message, exit 0. `say` output prints straight through.
- **VM only**: the runner is a dev tool and the VM is the reference
  semantics; WASM/native are compile targets and per-test toolchain
  spin-up would be slow and couldn't easily invoke individual test
  functions.

## Verification

- `tests/test_assert.py` (new): pass/fail/message/lazy-message,
  non-boolean condition and non-text message are type errors, assert in
  functions, VM + WASM differential on the failure message.
- `tests/test_test_runner.py` (new, 22 tests): discovery (nested dirs,
  both filename conventions, non-test files ignored), isolation
  (globals don't leak), error-raising test → failure with file:line,
  uncompilable file → clean failure entry, summary counts + exit
  codes, no-tests-found, `assert` with and without message through the
  real runner.
- `examples/testing/` (new): `math.niko` + `math_test.niko` +
  `README.txt` — a worked example verified on the real CLI.
- Full suite green: 26 Niko 1, 16 Niko 2 + nikoir round trip, all
  `tests/test_*.py`.

## Limits (future work, not half-built)

No fixtures, no mocks, no coverage. `assert` on the native backend is a
clean compile error. The runner is VM-only. Functions return `nothing`
unless they `give back` a value — test authors will hit this; it's in
the example README.
