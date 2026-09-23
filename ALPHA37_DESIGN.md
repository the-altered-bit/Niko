# Alpha 37 design — fix the VM `stop`/iterator-stack bug

## Goal

Fix the highest-value bug from the Alpha 36 fuzz campaign: `stop` out
of a `repeat`/`for each` loop leaked the loop's iterator on the VM's
`iter_stack`, so a `stop` nested inside another iterator loop corrupted
the outer loop's `..._NEXT` op — hang or silently wrong iteration
values. No language-semantics change: `stop` must mean exactly what it
means today, just without corrupting the machine.

## The mechanism (root cause)

Three facts combine into the bug:

1. **The VM drives `repeat`/`for each` from a per-frame iterator
   stack.** `ITER_REPEAT` / `ITER_PREP` push a Python iterator onto
   `f.iter_stack`; `REPEAT_NEXT` / `ITER_NEXT` advance `iter_stack[-1]`
   and pop it on `StopIteration`, jumping to the loop end.
   (`niko2/vm.py`, ~lines 227-235.)
2. **The compiler's `loop_stack` didn't record loop kinds.** Entries
   were `(start, breaks, conts)` 3-tuples. `stop` and `skip` both
   compiled to a bare `JUMP` recorded in the innermost loop's
   breaks/conts list, patched to the loop end (`stop`) or loop head
   (`skip`). (`niko2/compiler.py`, ~lines 74-101.)
3. **So `stop` jumped to the loop end without popping the iterator.**
   The stale iterator sat on `iter_stack`; the next enclosing
   `repeat`/`for each`'s `..._NEXT` consumed `iter_stack[-1]` — the
   dead iterator — instead of its own. In the fuzzer's minimal repro
   the outer `repeat`'s `REPEAT_NEXT` kept finding the inner `for
   each`'s unexhausted iterator, so the repeat never advanced: an
   infinite loop (leaking one iterator per cycle). Other shapes
   silently resumed the outer loop from the inner loop's leftover
   iterator — wrong values, no hang.

`skip` was correctly unaffected: it jumps to the loop head, i.e. the
`..._NEXT` op, where the live iterator is advanced. `stop` out of a
`while` was unaffected (no iterator). `stop` with no enclosing
iterator loop was unaffected (the leaked iterator was never consumed).

## The fix

Compiler + VM, minimal:

- `loop_stack` entries are now 4-tuples
  `(start, breaks, conts, kind)` with kind `'repeat'` / `'for'` /
  `'while'` (push sites in the `RepeatStmt`/`ForStmt`/`WhileStmt`
  branches; all unpack sites updated — the three loop pops and the
  `stop`/`skip` read site). The `FunctionDef` save/reset/restore and
  the initializer needed no change.
- In the `StopStmt` branch: when the innermost loop's kind is
  `'repeat'` or `'for'`, emit a new `ITER_POP` opcode **before** the
  break `JUMP`. `SkipStmt` and `stop`-out-of-`while` emit nothing
  extra.
- `niko2/vm.py`: `ITER_POP` → `f.iter_stack.pop()`, placed next to
  the other ITER ops.

Why exactly one pop, and why it's always correct: the iterator is
pushed before the loop body and stays live for the whole body, so at
any `stop` site (including `stop` inside nested `if`/`match` within
the body) exactly one of the stopped loop's iterators is on top of
the stack. `stop` breaks only the innermost loop (existing
semantics, unchanged), and only the innermost loop's iterator needs
popping — enclosing loops' iterators stay live and correct. The
normal-exhaustion paths already pop, so the pop belongs on the break
path only.

WASM and native were checked and are unaffected: neither has an
iterator stack (WASM lowers loops to structured `br` to labels;
native lowers to C loops where `break` cleans up naturally). Both
already terminated correctly on the repro. Untouched.

## As built

Exactly per the sketch — no deviation. The diff is 9 added lines in
`compiler.py` (4-tuple + the conditional `ITER_POP` emit) and 1 line
in `vm.py`. The implementer verified 9 shapes by hand (original
repro, `stop` from `repeat`-in-`for each` and `for each`-in-`repeat`,
doubly-nested `for each`, `stop`-in-`if`, `stop`-out-of-`while`,
`stop` with no enclosing iterator loop, `skip` in both iterator
loops, triple nesting) — all correct values, rc=0, and all 9
byte-identical across VM/WASM/native.

`tests/test_stop.py` (new, 31 differential + 2 compile-error cases)
follows the `test_loop_control.py` harness pattern: module-level
`CASES` of `(name, source, expected-stdout)`, VM via stdin, WASM and
native via temp files with timeouts. Two cases were mis-predicted
while writing (both author errors, not code bugs): an arithmetic
slip in an expected list, and referencing a loop variable outside its
scope — Niko 2 scopes loop variables to the loop body, which the
test now pins deliberately.

The fuzzer worker relaxed `_gen_loop_exit` (now free choice of
`stop`/`skip` — safe everywhere post-fix), removed the now-dead
`loop_kinds` tracking, and biased generation toward nested loops
with exits (in-loop stop/skip 0.06→0.10 per statement; while/for
0.06→0.08, repeat 0.04→0.08; funded by less `set`/`match` inside
loops). It also fixed a latent generator bug the new bias exposed:
`iterated` was a set, so a nested `for each` over the same list var
called `discard` on exit and cleared the outer loop's claim, letting
`put`/`remove` target a list an enclosing loop was iterating — now a
refcount dict (0 violations over 4,000 programs / 40 seeds).
`tests/test_fuzz.py`'s old pin
(`test_fuzz_stop_only_where_iterator_leak_is_harmless`) became
`test_fuzz_stop_free_in_nested_loops`, asserting the previously
forbidden nested stops are actually generated (29 per 300 programs
on seed 4242).

## Campaign results

Focused campaign: 3,000 cases (seeds 3711–3714 × 750, `--backend
vm,wasm,native --native-sample 10 --timeout 15`): **2,936 pass, 64
fail, 0 hangs, 0 generator rejects — zero VM-vs-WASM divergences on
all 3,000.** All 64 failures triaged into known classes: 58 × native
`otherwise if` miscompile (55 loud invalid-C, 2 silent wrong-branch,
1 silent wrong-branch surfacing as a runtime Niko error — verified
the same bug: native wrongly evaluates the otherwise-if condition),
6 × native `ask number` missing reprompt. No new backend bugs.

Corpus re-run: the 34 surviving Alpha 36 `fuzz_failures/` repros —
none were stop/iterator-class; VM+WASM agree 34/34; native failures
are the two known classes above.

## Limits and follow-ups (not this sprint)

- The other 6 fuzzer-found bugs stay in `KNOWN_LIMITATIONS.md` for
  future sprints (native `otherwise if` miscompile is the natural
  next fix — invalid C output).
- Repo-level observation from testing: `tests/test_dap.py` sets a
  module-level `signal.alarm(900)`, and the full suite now exceeds 15
  minutes (mostly module-level native compiles at import), so a
  single `pytest tests/` run gets SIGALRM-killed. The suite was run
  split (`--ignore=tests/test_dap.py` + `test_dap.py` alone), all
  green. Worth scoping that alarm to the DAP tests in a future
  tooling pass.
