# Release notes — Alpha 36: `niko2 fuzz`, the differential fuzzer

Three backends (VM, WASM, native) should run every Niko program
identically. Alpha 36 makes that check push-button: `niko2 fuzz`
generates random Niko programs from a seeded grammar-aware generator,
runs each on every backend as a subprocess, and compares stdout bytes
exactly. A disagreement is shrunk to a minimal repro and saved with a
report:

```bash
niko2 fuzz --seed 1 --cases 1000            # generate + compare (seed always printed)
niko2 fuzz --backend vm,wasm                # subset of backends
niko2 fuzz --native-sample 10               # native only on every 10th case (~3s/compile)
niko2 fuzz --corpus fuzz_failures           # re-run saved cases as a regression set
```

Exit 0 when every case agrees, 1 on divergences, 2 on usage errors.
The generator covers arithmetic, comparisons, `and`/`or`/`not`, `if`,
`while`, `for each`, `repeat`, `match` (+ guards and patterns),
functions/closures/recursion, lists/records and their mutation,
multibyte strings, `say`, `ask` with canned stdin, and the
`ok`/`error`/`try_*` builtins — while guaranteeing termination by
construction (bounded loop counters, no `ask` inside loops, small
literals for exponential-time calls), so a hang is always a backend
bug, never a generator artifact.

## What changed

- **`niko2/fuzz.py`** (new, ~1,400 lines): the generator (seeded RNG,
  tracks a static type environment so programs typecheck; candidates
  validated in-process before running), the differential runner
  (per-backend timeout — a hang is a failure, never silent), the greedy
  line-deletion minimizer, and the campaign driver.
- **CLI** (`niko2/cli.py`): `fuzz` subcommand with `--seed`, `--cases`,
  `--backend`, `--timeout`, `--native-sample`, `--keep-passing`,
  `--no-minimize`, `--corpus`.
- **`niko2/backends/wasm.py`** (one-line drive-by): `say error("x")`
  now renders `error("x")` on WASM too — byte-identical with VM and
  native.
- **`niko2/KNOWN_LIMITATIONS.md`**: seven real backend bugs the fuzzer
  found, with minimal repros — the native backend miscompiles
  `otherwise if` with a temp-emitting condition (emits invalid C, plus a
  silent wrong-branch variant), `ask` past stdin EOF diverges three ways
  (VM errors cleanly; WASM/native yield `""` for text; both compiled
  backends hang forever on `ask number`),
  `error_message(try_number(bad-text))` differs VM-vs-WASM, native skips
  re-printing the prompt on `ask number` retry, `stop` out of
  `repeat`/`for each` leaks the VM's loop iterator (hangs or silently
  wrong iteration when nested), and comparing `yes`/`no` with numbers
  diverges (VM uses Python bool<int ordering; WASM/native raise).
  None are fixed this sprint (beyond the drive-by budget); the fuzzer
  avoids each class by construction. Three generator bugs the campaign
  exposed are fixed (`put` into an iterated list, `stop` in nested
  loops, mistyped early `give back`).
- **`tests/test_fuzz.py`** (new): fixed-seed 20-case VM+WASM smoke
  campaign plus structural tests pinning the termination guarantees.
  Native is excluded from the committed test until the `otherwise if`
  codegen bug is fixed; campaign runs include it, sampled.
- **`.gitignore`**: `fuzz_failures/`, `fuzz_work/`, `fuzz_corpus/` are
  local campaign scratch, never committed.

## Campaign results

Final campaign: 5,000 cases (seeds 101–105), VM vs WASM, final generator:
**5,000/5,000 pass, 0 failures, 0 known-divergences, 0 generator
rejects.** A 200-case native characterization run (seed 201, all three
backends) found nothing beyond the documented `otherwise if` codegen
bug. The bug-finding runs with pre-fix generators surfaced 7 real
backend divergences (listed above), including a genuine VM control-flow
bug (`stop` leaking the loop iterator) — all documented, none fixed
this sprint. The first real run had already paid for the whole alpha:
seed 20260923, case 10 surfaced the native miscompile on day one.

## Limits

- Generated programs avoid nondeterminism by rule (no clock, random,
  file I/O, `pkg:`, `sleep`); floats are never printed directly.
- The minimizer is line-granular and greedy; all-backends-error with
  the same return code counts as agreement (error text differs by
  design across backends).
- Native runs cost ~3.3s per case (C compile dominated) — sample it.

See `ALPHA36_DESIGN.md` for the full design, the termination
argument, the resume notes, and the campaign log.
