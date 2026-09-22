# Niko 2.0 — Alpha 30: short-circuit `and`/`or` (Niko 1's rule)

Alpha 30 changes **no syntax, no builtins** — it changes semantics,
and only in one place: `and`/`or` finally behave like Niko 1.
Until now Niko 2 evaluated **both** operands of `and`/`or` on all
three backends and always returned a strict boolean. Niko 1 (a Python
transpiler that passes `and`/`or` through verbatim) short-circuits
and returns an operand. Alpha 30 closes that divergence on
VM/WASM/native, byte-identically. The SSG guard idiom
`if i < n and item (i + 1) of s is "*":` works without the nested-`if`
workaround.

## The rule

- `a and b`: evaluate `a`; if falsy, the result is `a` and `b` is
  never evaluated; otherwise the result is `b`.
- `a or b`: evaluate `a`; if truthy, the result is `a` and `b` is
  never evaluated; otherwise the result is `b`.
- Falsy: `no`, `nothing`, `0`, `0.0`, `""`, `[]`, `{}`. Everything
  else is truthy. So `"" or "default"` → `"default"`,
  `0 and f()` → `0` without calling `f`, `yes and 42` → `42`.
- Precedence unchanged: `and` binds tighter than `or`.

## What changed

**Typechecker** (`niko2/typecheck.py`): `and`/`or` accept any operand
types (the old `'{op} requires booleans'` error is gone). The result
type is `BOOLEAN` only when both operands are `BOOLEAN`, else `ANY` —
the honest static type of "one of the operands". `if`/`while`/`assert`
conditions and match guards already accept `ANY`, so no other checker
changes were needed.

**VM** (`niko2/compiler.py` + `niko2/vm.py`): jump-based codegen
mirroring CPython. New opcodes `JUMP_IF_FALSE_OR_POP` /
`JUMP_IF_TRUE_OR_POP`: the left value stays on the stack as the
result when it decides; otherwise it is popped and the right side is
evaluated for the result. Truthiness uses the VM's existing `bool()`,
which is Python truthiness on the VM's Python values — Niko 1's rule
by construction. The old `bool(a) and bool(b)` `BINARY` branches are
removed.

**WASM** (`niko2/backends/wasm.py`): new `_gen_short_circuit` —
`gen(left)` into a fresh result local, `call truthy` (the existing
host helper implementing Niko 1 truthiness per tag), `br_if $end`
with `i32.eqz` only for `and`, right side evaluated into the same
local only when reached. Exactly one value pointer leaves the block
in both paths; bump-allocated memory, nothing to free. Loop-stack
(`stop`/`skip`) labels are unaffected.

**Native** (`niko2/backends/native.py`): C temp + branch following the
`MatchExpr` temp pattern — `NVal *t = <left>; if (nval_truthy(t)) { t
= <right>; }` for `and` (negated for `or`). C's `&&`/`||` can't
express operand-value semantics on `NVal*`, and the runtime never
frees (GC-less, malloc-everything), so reassigning the temp is
scheme-correct. **Follow-on fix**: the temp emission exposed a
latent bug — the native `while` evaluated its condition once into
the `while()` parens, so a statement-emitting condition (`a and b`)
would capture a stale temp and loop forever. The native loop is now
`while (1) { <cond stmts>; if (!nval_truthy(c)) break; … }`, matching
the WASM loop/block structure; equivalent for pure expressions.

**Constant pool** (`niko2/ir.py`): `IRBuilder.const` deduped with
Python `==`, so `True`/`1`, `False`/`0`, `1`/`1.0` collided — known
since Alpha 8, invisible while `and`/`or` returned booleans. With
operand-returning semantics `say yes and 1` printed `yes` (the `1`
literal reused the `True` slot). Fixed: dedupe only when
`type(c) is type(v) and c == v`, local to this one function.
(WASM/native walk the AST directly; unaffected.)

## Verification status

- `tests/test_shortcircuit.py` (new): 3-way VM/WASM/native
  differential — truth tables per type, the `True`/`1` dedup cases,
  skipped-side-never-evaluates (side-effect function + `1/0` never
  fire), chained/mixed precedence, contexts (`if`, `while` with
  `stop`/`skip`, `say`, call args, match guard, nested expressions),
  the SSG guard idiom in both failing and hitting form, error
  attribution (taken right operand's error fires; skipped right
  operand's error can never fire; a check-time error in the right
  operand reports its own line/column), and the typechecker rule
  (`1 and 2` checks cleanly, `yes and no` is still `BOOLEAN` in an
  `if`). All assertions green.
- Niko 1 (`tests/cases/logic.niko` + `.out`): 13 new lines proving
  Niko 1's own rule — value semantics for every type plus the
  never-call short-circuit case. Output captured from `niko.py`
  itself; 26 cases, 0 failures.
- Honest boundary kept: `yes * 2` still differs (native refuses
  number-only ops; VM computes Python's `2`) — the differential
  caught this during development and the suite avoids boolean
  arithmetic, as before. Runtime errors still carry no line number
  in `niko2 run` output (check-time errors do).
- Full suite green: `tests/run_tests.py` (26), 
...[truncated 1441 chars]