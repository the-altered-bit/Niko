# Alpha 36 design — `niko2 fuzz`, the differential fuzzer

## Goal

Give Niko 2 a push-button way to answer "do all three backends (VM,
WASM, native) agree?" A seeded grammar-aware generator produces random
Niko programs; the runner executes each on every backend as a
subprocess and compares stdout bytes exactly. Divergences are shrunk
and saved with a report. The point is backend conformance, not language
design: no language-semantics changes ship in this alpha.

## Architecture

`niko2/fuzz.py` (one module, ~1,400 lines):

- **Generator** (`Gen`): seeded `random.Random`, tracks a tiny static
  type environment so emitted programs pass the typechecker —
  every variable/function has a known type, indices stay in range,
  division is never by zero, `unwrap` never sees an error.
- **Validation** (`valid_program`): every candidate is parsed +
  typechecked in-process before it runs. Invalid candidates are
  discarded and counted, never executed. The check mirrors
  `cli.compile_source` exactly (top-level `.name` attributes
  pre-registered as ANY — that *is* what the real pipeline does via
  `collect_imported_names`, verified during the audit).
- **Runner** (`run_backend`/`classify`): per-backend subprocess with a
  timeout; results are `(rc, stdout_bytes)`. A hang is always a
  failure, never silent. `classify` returns pass / known (matches an
  `EXPECTED_DIVERGENCES` entry) / fail.
- **Minimizer** (`minimize`): greedy line deletion — delete single
  lines while the program still parses/checks AND still fails with the
  identical backend signature. Line-granular, greedy (local minima
  possible), assumes determinism, max 3 passes. The minimized program
  is re-run to confirm it still fails before saving.
- **CLI** (`niko2 fuzz`): `--seed` (always printed, default random),
  `--cases`, `--backend`, `--timeout`, `--native-sample`, 
  `--keep-passing`, `--no-minimize`, `--corpus` (re-run saved cases).
  Exit 0 = all agree, 1 = divergences, 2 = usage error.

## Coverage

Arithmetic (`+ - * % **`), comparisons (all English and symbolic
forms), `and`/`or`/`not` with operand-returning semantics,
`if`/`otherwise if`/`otherwise`, `while`, `for each`, `repeat`,
`match` (statement and expression forms, guards, literal/alternative/
binding/list-head-tail/result/record patterns), functions, closures,
recursion (`fact`/`fib` templates), lists (`item_of`, `[]`-indexing,
`put`/`remove`/`set item`), records (`{k: v}`, `["k"]` set),
multibyte strings (literals, indexing, `upper`/`lower`/`trim`/
`replace`/`split`/`count_of`), `say`, `ask`/`ask number` with canned
stdin, `ok`/`error`/`unwrap_or`/`is_ok`/`is_error`/`error_message`/
`try_number`, and the `try_*` builtins. Integers stay small
(|v| < 2**53); floats are never printed directly, so the two
documented rendering divergences (f64 precision, integral-float
`text()`) cannot fire by construction. The runner still carries a
tiny `EXPECTED_DIVERGENCES` allowlist (currently: the integral-float
rendering split) for anything that slips through.

## Termination by construction

The pilot run (seed 1) produced two hang classes, both generator bugs,
both fixed before the campaign:

1. **While-loop counter reassignment.** The pilot emitted `set c1:
   number to -357` inside the loop body, resetting the counter every
   iteration. Fix: the counter lives in a `protected` set while the
   body generates; `gen_set` *and* `gen_mutate` (`add`/`take`) refuse
   to touch protected names.
2. **Unbounded exponential calls.** The pilot emitted `f1(number("4271"))`
   (naive-recursive fib) and `say f1(208)` in a match guard. Fix:
   `fib`/`fact` names are registered in `Gen.expensive`; every call
   site (statement-level `gen_call_stmt` and expression-level
   `_call_expr`) passes small literals (0..12) for them. 12! is still
   exactly representable in f64, so this also keeps the "integers stay
   small" invariant true. Expression-level calls additionally take
   terminal-only arguments, so calls cannot nest inside expressions.

Plus: `for each`/`repeat` iterate over bounded literals; `ask` is never
generated inside a loop or function body, so the runtime ask count can
never exceed the canned stdin lines (stdin exhaustion diverges across
backends — see KNOWN_LIMITATIONS.md); `numbers a to b` counts down when
a > b on all backends (verified in VM/WASM/native sources).

Net: any hang the campaign reports is a real backend bug, not a
generator artifact. The committed smoke test (`tests/test_fuzz.py`)
pins all three guarantees structurally (counter scan, ask block-stack
check, tiny-literal call check over hundreds of generated programs).

## As built

### Resume note (2026-09-23)

A first worker built `niko2/fuzz.py` (generator + runner + minimizer),
the `niko2 fuzz` CLI wiring, the WASM `error("` drive-by fix, and ran
a seed-1 pilot that saved 3 failures — then died in a daemon restart
before docs/tests/campaign/commit. The resume (this alpha's author):

- Audited all 1,388 lines; verified `valid_program` against the real
  `compile_source` pipeline (the `.name` pre-registration is faithful,
  not a hack); verified the WASM `error("x")` fix renders identically
  on all three backends.
- Fixed the three termination holes above (protected set was
  `gen_set`-only — `add`/`take` could still reset counters; fib/fact
  args were unbounded; `ask` was allowed in loop bodies).
- Re-ran the 3 pilot failures through the fixed runner: `fuzz_1_5` and
  `fuzz_1_9` still hang on VM+WASM (genuinely non-terminating programs
  the fixed generator can no longer emit — reclassification:
  generator bugs, fixed); `fuzz_1_6` reproduced as VM rc=1 vs WASM
  hang — reclassified as a **real backend divergence** (stdin
  exhaustion), now in KNOWN_LIMITATIONS.md.
- Added `tests/test_fuzz.py`, all docs, the campaign, and the ship.

### Campaign results (2026-09-23)

- **Final campaign**: seeds 101-105, 1,000 cases each = **5,000 cases**,
  VM vs WASM, timeout 15s, final generator -- **5,000/5,000 pass,
  0 failures, 0 known-divergences, 0 generator rejects**.
  Per-case cost ~1.2s (VM+WASM).
- **Native characterization**: seed 201, 200 cases, all three backends
  (`--no-minimize`), final generator -- 166/200 all-agree; the 34
  failures triaged to known bugs only: 31x native(rc=1) = the
  `otherwise if` invalid-C codegen bug, 2x the native `ask number`
  prompt-skip divergence, 1x the `otherwise if` silent wrong-branch
  variant. The VM-vs-WASM leg agreed on all 200. Native costs ~3.3s/case
  (C compile dominated), hence the sampling.
- **Bug-finding runs** (pre-fix generators) surfaced **7 real backend
  divergences**, all now in `niko2/KNOWN_LIMITATIONS.md` with minimal
  repros, none fixed this sprint (beyond the drive-by budget):
  1. **Native miscompiles `otherwise if` with a temp-emitting
     condition** -- `gen_expr(cond)` emits temp assignments between
     the previous `}` and the `else if` line (invalid C). Found on the
     campaign's first real run (seed 20260923, case 10); fix sketch in
     the entry.
  2. **Native `otherwise if` silent wrong-branch variant** -- same root
     cause, but the C is valid and the wrong branch runs (seed 201,
     cases 114/125/160; minimized to `/tmp/oi1.niko`). Worse than the
     compile error: no signal, just wrong output.
  3. **`ask` past stdin EOF diverges three ways** -- VM errors cleanly
     (rc=1), WASM/native yield `""` for text asks, and both compiled
     backends **hang forever** (`Please type a number.` reprompt) on
     `ask number`. The fuzzer avoids the class by construction.
  4. **`error_message(try_number(bad-text))` differs VM-vs-WASM** -- VM
     builds `I can't turn 'x' into a number.`, WASM/native emit the
     bare input (seed 201 case 140; a derived form via `replace(m, ...)`
     surfaced in seed 101 case 507). The fuzzer now generates
     `try_number` with valid inputs only.
  5. **Native skips re-printing the prompt on `ask number` retry**
     (seed 201 case 22).
  6. **VM `stop` out of `repeat`/`for each` leaks the loop iterator**
     -- hangs (or silently wrong iteration) when nested in another
     loop (seed 777 case 2; minimized to `/tmp/m1.niko`). **Flagged
     prominently**: a genuine VM control-flow bug, not an output diff.
     The fuzzer emits `stop` only where the leak is harmless.
  7. **VM vs WASM/native: comparing `yes`/`no` with numbers** -- VM
     inherits Python's `bool < int` ordering (`min(-733, no)` -> `-733`);
     WASM/native raise `I can't compare those values.` (seed 101 case
     178). Typechecker accepts these; semantics question, out of scope.
- **Generator bugs fixed** (3, all found by the campaign): `put` into a
  list an enclosing `for each` is iterating (hang; `self.iterated`
  set); `stop` in nested `for`/`repeat` (VM iterator leak;
  `self.loop_kinds` stack + `_gen_loop_exit` rule); early `give back`
  in function bodies used a random type, drifting the generator's
  return-type model (`self.func_ret`). All pinned by structural tests
  in `tests/test_fuzz.py`.
- **Drive-by fixes** (2, both one-liners): WASM `error("x")` rendering
  gained its closing paren (now identical on all backends); `.gitignore`
  covers the fuzzer's scratch dirs.

### What the fuzzer does not do (deliberate)

- No wall-clock, randomness, file I/O, `pkg:`, or `sleep` in generated
  programs (nondeterminism would be false positives).
- The minimizer is line-granular and greedy; `classify` treats
  all-backends-error-with-same-rc as agreement (error text differs by
  design).
- `fuzz_failures/` / `fuzz_work/` / `fuzz_corpus/` are local scratch,
  gitignored, never committed.

## Files

- `niko2/fuzz.py` — generator, runner, minimizer, CLI entry
- `niko2/cli.py` — `fuzz` subcommand + flags
- `niko2/backends/wasm.py` — one-line `error("` rendering fix
- `niko2/KNOWN_LIMITATIONS.md` — 2 new entries (native otherwise-if,
  ask-at-EOF)
- `tests/test_fuzz.py` — fixed-seed smoke (20 cases VM+WASM) +
  structural termination-guarantee tests
- `.gitignore` — fuzzer scratch dirs
