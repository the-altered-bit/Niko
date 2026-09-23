# Alpha 38 design — native `otherwise if` miscompile fix

## The bug (from the Alpha 36 fuzz campaign)

Two failure modes, one root cause, both in the native backend's `IfStmt`
codegen (`niko2/backends/native.py`, `gen_stmt`):

1. **Loud**: an `otherwise if` whose condition needs temp variables
   (list/record construction, builtin calls like `length([...])`)
   emitted C that failed to compile: `'else' without a previous 'if'`.
2. **Silent**: an `otherwise if` whose condition contains `and`/`or`
   compiled cleanly but attached the chain's `else if`/`else` to the
   *wrong* `if` — after the first branch ran, a later branch's body ran
   too.

## Mechanism

`gen_expr` is statement-emitting: complex expressions produce C
*statements* (temp declarations/assignments) as a side effect, appended
to the output stream before the returned C expression is used. The old
`IfStmt` codegen did, per branch:

```python
c = self.gen_expr(cond)          # temp statements land HERE, in the stream
self._emit(f'{kw} (nval_truthy({c})) {{')   # kw = 'else if' for branch 2+
```

For branch 2+, the temp statements landed between the previous branch's
closing `}` and the `else if` line. Two consequences:

- **Loud**: `}` then `NVal *t_2 = ...;` then `else if (...)` — `else`
  with no immediately preceding `if` is a compile error.
- **Silent**: the `and`/`or` lowering emits `NVal *t = <lhs>;` then
  `if (!nval_truthy(t)) { t = <rhs>; }`. The chain's `else if
  (nval_truthy(t))` then parsed as the else-branch *of that inner if*
  (dangling else), not of the outer chain. After branch 1 executed, the
  temps still ran unconditionally and the mis-attached branch fired.

Plain `if` (with or without `otherwise`) never broke: temps before a
leading `if` sit at statement position, which is legal C. Only the
`else if` continuation was fragile. The WASM backend never had this
pattern; `_gen_match` deliberately avoids else-chains (it uses
`goto match_end_*`), so match was unaffected.

## The fix (as built)

New `_gen_if_chain(branches, otherwise)` in `niko2/backends/native.py`,
called from `gen_stmt`'s `IfStmt` arm. Each branch after the first is
nested inside the previous branch's `else { ... }` block instead of a
flat `else if` ladder:

```c
if (nval_truthy(c1)) {
    /* body1 */
} else {
    /* temp statements for c2 land here -- legal */
    if (nval_truthy(c2)) {
        /* body2 */
    } else {
        /* otherwise body, or deeper nesting */
    }
}
```

The `and`/`or` lowering's inner `if (!t) { t = rhs; }` is now a complete
statement inside the else block, followed by a fresh `if
(nval_truthy(t))` — no dangling else possible. Conditions still evaluate
lazily in order (each sits inside the previous else), so short-circuit
semantics are preserved exactly, including the never-evaluate-later
guarantee (proven by the `short_circuit` test: `item_of(99, "ab")` in a
later condition never runs when an earlier branch is taken).

Scoping note: temps and branch-local `set` declarations were already
block-scoped inside `{ ... }` bodies; nesting adds depth but no new
scope behavior. `stop`/`skip` (`break`/`continue`) are unaffected by
the extra brace depth — `loop_depth` tracking is unchanged.

## As built

- The fix is ~25 lines (method + call site) in `niko2/backends/native.py`
  only. No shared-code changes; VM and WASM untouched.
- `tests/test_native_otherwise.py` (new, module-level 3-way loop in the
  `test_wasm_unicode.py` style): 13 cases, each asserting VM output first
  (reference), then byte-identical WASM (if node) and native (if cc).
  The native leg runs `niko2 native --run`, so a compile failure is a
  test failure by construction.
- `tests/test_fuzz.py`: native re-enabled in the committed smoke test
  (was excluded pending this fix), sampled 1-in-10 to keep CI fast.
- Fuzzer: `Gen(if_bias=...)` + `niko2 fuzz --if-bias` biases generation
  toward deeper `otherwise if` chains (1–4 branches at 95% vs the
  default 0–2 at 70%). Default generator distribution unchanged
  (`if_bias=0` default), so existing seeds reproduce identically.

## Adjacent observation (not fixed, out of scope)

While building tests: a `match` *expression* as an `otherwise if`
condition does not parse (`otherwise if match x:` → `unexpected ...
after expression` at the first `when`). This is a loud, uniform parse
error on all backends — not a backend divergence — so it is out of
scope for this sprint. Noted here for a future parser sprint.

## Verification

- Both fuzzer repros fixed: the loud variant compiles and prints `1`;
  the silent variant prints `first` / `1`, byte-identical to the VM.
- `tests/test_native_otherwise.py`: 13/13 green (3-way each).
- Focused campaign: `niko2 fuzz --if-bias --cases 3000 --seed 3801
  --backend vm,wasm,native --native-sample 10` — 3,000 `--if-bias` cases (seeds 3801-3804, all three backends, native sampled 1-in-10): 2,995 pass, 5 failures -- all 5 triaged to the documented native `ask number` re-prompt divergence (out of scope); zero `otherwise if` divergences, zero invalid-C emissions, zero hangs.
- Full suite: 402 pytest + `tests/test_native_otherwise.py`'s 13 three-way cases, 26 Niko 1, 16 Niko 2 + nikoir round trip.
