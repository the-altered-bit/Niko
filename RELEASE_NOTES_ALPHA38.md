# Release notes — Alpha 38: native `otherwise if` miscompile fix

The Alpha 36 fuzzer found the scariest remaining backend bug: the native
backend miscompiled `otherwise if` chains whose conditions needed
temporary variables. Two failure modes — one loud, one silent:

```niko
if 1 is 1:
    say 1
otherwise if length([1, 2]) is 2:
    say 2
otherwise:
    say 3
```

Before: the native backend emitted C that didn't compile (`'else'
without a previous 'if'`). And worse, a sibling variant compiled cleanly
but silently ran the wrong branch — after the first branch printed, the
third branch's body ran too. Alpha 38 fixes both.

## What changed

- **Native backend** (`niko2/backends/native.py`): `if`/`otherwise if`/`otherwise`
  chains are now emitted *nested* — each branch after the first lives
  inside the previous branch's `else { ... }` block (new `_gen_if_chain`),
  instead of a flat `else if` ladder. Temp-variable statements from
  condition codegen now land legally inside the `else` block, and the
  `and`/`or` lowering's inner `if` can no longer capture the chain's
  `else if`. Conditions still evaluate lazily in order, so short-circuit
  semantics are unchanged.
- **Fuzzer** (`niko2/fuzz.py` + `niko2/cli.py`): new `--if-bias` flag
  (`Gen(if_bias=...)`) makes the generator emit deeper `otherwise if`
  chains for focused campaigns. `tests/test_fuzz.py` smoke test runs
  native again (sampled 1-in-10) — it was excluded pending this fix.

No language semantics changed — the VM (the reference) already behaved
this way; native now matches it. The WASM backend never had this bug
pattern.

## Verification

- New `tests/test_native_otherwise.py`: 13 three-way differential cases
  (VM/WASM/native byte-identical) — both fuzzer repros, 3–4 branch chains
  with temp-emitting conditions, `and`/`or`/call/record-literal
  conditions, chains nested in loops and functions, laziness
  (short-circuit) proof, chain-in-chain, plain `if`/`otherwise`
  regression guard. The native leg inherently asserts the C compiles.
- Focused fuzz campaign: 3,000 `--if-bias` cases across all three
  backends — **zero divergences, zero invalid-C emissions** 3,000 `--if-bias` cases (seeds 3801-3804, all three backends, native sampled 1-in-10): 2,995 pass, 5 failures -- all 5 triaged to the documented native `ask number` re-prompt divergence (out of scope); zero `otherwise if` divergences, zero invalid-C emissions, zero hangs.
- Full suite green 402 pytest + `tests/test_native_otherwise.py`'s 13 three-way cases, 26 Niko 1, 16 Niko 2 + nikoir round trip.

See `ALPHA38_DESIGN.md` for the exact miscompile mechanism.
