# Alpha 30 design: short-circuit `and` / `or`

## Problem

Niko 2's `and`/`or` evaluate **both** operands on all three backends
and always return a boolean (`vm.py`: `bool(a) and bool(b)`). Niko 1
short-circuits and returns an operand. Guard idioms like
`if i < n and item (i + 1) of s is "*":` raise in Niko 2
(the SSG worked around it with nested `if`s). Recorded in
`niko2/KNOWN_LIMITATIONS.md` ("Alpha 28 dogfood notes") as future work.

## Niko 1's exact rule (read from `niko.py`, not assumed)

Niko 1 is a transpiler: `words()` rewrites English phrasing to Python
but has **no rewrite for `and`/`or`** — they pass through verbatim to
Python. `nothing` → `None`, `yes` → `True`, `no` → `False`. Therefore
Niko 1's `and`/`or` **are** Python's `and`/`or`:

- `a and b`: evaluate `a`; if `a` is falsy, the result is `a` and `b`
  is never evaluated; otherwise the result is `b`.
- `a or b`: evaluate `a`; if `a` is truthy, the result is `a` and `b`
  is never evaluated; otherwise the result is `b`.
- Falsy: `no`, `nothing`, `0`, `0.0`, `""`, `[]`, `{}`. Everything
  else is truthy. (Python truthiness on Niko 1's value mapping.)
- Precedence: `and` binds tighter than `or` (already true in Niko 2's
  parser: `PRE={'or':1,'and':2}` — matches Python).

Compatibility wins: Niko 2 implements exactly this, even where
surprising (e.g. `"" or "default"` → `"default"`, `0 and f()` → `0`
without calling `f`).

## Changes

**Typechecker** (`niko2/typecheck.py`): `and`/`or` accept any operand
types (Niko 1 has no typechecker). Result type: `BOOLEAN` if both
operands are `BOOLEAN` (preserves today's precise typing for the
common case), else `ANY` (the honest static type of "one of the
operands"). `if`/`while`/`assert` conditions already accept `ANY`, so
no changes needed there.

**VM** (`niko2/compiler.py` + `niko2/vm.py`): special-case `and`/`or`
in `Compiler.expr`'s `BinaryExpr` branch with jump-based codegen.
New opcodes mirroring CPython:

- `JUMP_IF_FALSE_OR_POP target`: if top-of-stack is falsy, jump to
  target leaving the value on the stack; else pop it and continue.
- `JUMP_IF_TRUE_OR_POP target`: if top-of-stack is truthy, jump to
  target leaving the value on the stack; else pop it and continue.

Codegen: `a and b` → `expr(a)`; `JUMP_IF_FALSE_OR_POP end`;
`expr(b)`; `end:`. `a or b` → `expr(a)`;
`JUMP_IF_TRUE_OR_POP end`; `expr(b)`; `end:`. The skipped operand is
never evaluated (no code runs for it at all). Truthiness uses the
same `bool()` the VM already uses for `JUMP_IF_FALSE`, which is
Python truthiness on the VM's Python values — exactly Niko 1's rule.
`nikoir.py` serializes opcodes generically (`op`/`arg`/`line`), so
the new opcodes round-trip with no changes. The old
`bool(a) and bool(b)` branches in `VM.binary` become dead once the
compiler stops emitting `BINARY` for `and`/`or` — remove them if
nothing else emits those ops, else leave with a comment.

**WASM** (`niko2/backends/wasm.py`, `_gen_expr` `BinaryExpr`):
special-case before the host-`binary` path, using the existing
`truthy` helper (already implements Niko 1 truthiness per tag) and
the writer's block/`br_if` API:

- `a and b`: `gen(left)`; `local.tee $t`; `call truthy`; `i32.eqz`;
  `br_if $end`; `drop`; `gen(right)`; `$end:`
- `a or b`: `gen(left)`; `local.tee $t`; `call truthy`; `br_if $end`;
  `drop`; `gen(right)`; `$end:`

At `$end` the stack holds exactly one value pointer in both paths
(the left pointer when short-circuited, the right pointer otherwise).
Bump-allocated memory: the dropped left pointer needs no freeing.

**Native** (`niko2/backends/native.py`, `gen_expr` `BinaryExpr`):
C's `&&`/`||` can't express operand-value semantics on `NVal*`, so
follow the existing `MatchExpr` temp pattern (emit statements, yield
a temp):

```c
NVal *t = <gen(left)>;
if (nval_truthy(t)) { t = <gen(right)>; }   /* and */
NVal *t = <gen(left)>;
if (!nval_truthy(t)) { t = <gen(right)>; }  /* or */
```

Verify against `niko_runtime.c`/`backends/niko_runtime.h` whether
`NVal` needs releasing on reassignment (if the runtime never frees,
reassignment is fine).

**Line numbers**: the jump/branch instructions are emitted with the
`BinaryExpr`'s line; errors inside either operand carry that
operand's own line from its nested codegen. A runtime error in the
*skipped* operand can never fire (its code never runs).

## As built

(appended by the final worker; everything below is verified against the
tree and the test suite)

### Niko 1's exact rule, confirmed by the new cases

`tests/cases/logic.niko` grew 13 lines (+10 output lines) and Niko 1's
output nails the rule down: `say "" or "d"` → `d`, `say 0 and 99` →
`0`, `say 0.0 or "z"` → `z`, `say nothing or "n"` → `n`,
`say no or "x"` → `x`, `say yes and 42` → `42`,
`say no and no` → `no` — and a function that would `say "BOOM"` if
called is never invoked by `say no and sc_bomb()` / `say yes or sc_bomb()`.
Niko 1 is a Python transpiler with no rewrite of `and`/`or`, so this
*is* Python semantics: operand-returning, truthiness = Python
truthiness (`no`/`nothing`/`0`/`0.0`/`""`/`[]`/`{}` falsy, everything
else truthy), and the right side never evaluated when the left side
decides. Niko 2 now matches all of it, on all three backends.

### Per-backend mechanism, as implemented

- **VM**: exactly the design. `JUMP_IF_FALSE_OR_POP` /
  `JUMP_IF_TRUE_OR_POP` in `vm.py` reuse `bool()` — the same Python
  truthiness the VM's values already have — so the VM's rule is Niko
  1's rule by construction. The old `bool(a) and bool(b)` branches
  in `VM.binary` are already gone (removed when the compiler stopped
  emitting `BINARY` for `and`/`or`); nothing else emits that opcode
  for these operators.
- **WASM**: `_gen_short_circuit` follows the design with one shape
  tweak — instead of `local.tee` + `drop`, the left value goes
  through a fresh result local (`local_set`, `local_get`), an empty
  `block`, and `br_if $end` off the `truthy` call (with `i32.eqz`
  only for `and`); the right side, when evaluated, is `local_set`
  into the same local. Both paths leave exactly one value pointer,
  read back with a final `local_get`. Same bump-allocated-memory
  reasoning: no freeing. Truthiness goes through the existing
  `truthy` host helper, which implements the Niko 1 definition per
  tag. Loop-stack labels (`stop`/`skip`) are absolute label objects
  from the writer, so opening/closing the block inside this call
  cannot disturb them.
- **Native**: C temp + branch, exactly the design. Fresh `_tmp()` per
  `and`/`or` keeps nested chains (`a and b and c`) safe; the skipped
  operand's C code never runs. No refcount to maintain — the runtime
  never frees `NVal*` (GC-less, malloc-everything), so reassigning the
  temp is scheme-correct.

### The `ir.py` constant-pool dedup fix (why it became necessary)

`IRBuilder.const` used `self.constants.index(v)`, which is Python
`==`: `True == 1`, `False == 0`, and `1 == 1.0` all compare equal, so
the `1` literal in `say yes and 1` deduped onto the `True` constant
slot and the program printed `yes`. Known since Alpha 8 but harmless
there because every `and`/`or` returned a strict boolean. Alpha 30's
operand-returning semantics makes the collision user-visible, so it
is fixed here: dedupe only when `type(c) is type(v) and c == v`
(10-line comment included), local to this one function.
`say yes or no` → `yes`, `say 1` → `1`, `say yes and 1` → `1` on the
VM. The WASM/native backends never shared this pool (they walk the
AST directly), so they are unaffected.

### The native `WhileStmt` follow-on fix

The native `and`/`or` temp emission exposed a latent bug: the native
`while` compiled its condition once, into the `while()` parens
(`while (nval_truthy(c))`), and a statement-emitting condition like
`a and b` (whose codegen now emits statements *before* yielding a
temp) would capture the temp once before the loop and loop forever /
never re-test. Fixed in the same sprint: the native loop is now
`while (1) { <cond stmts>; if (!nval_truthy(c)) break; ... }`, like
the WASM backend's loop/block structure. For pure expressions this is
exactly equivalent to `while (nval_truthy(c))`. Covered by
`ctx_while` / `ctx_while_stop_skip` in `tests/test_shortcircuit.py`
(including `stop`/`skip` under `and`/`or` conditions, 3-way).

### What is NOT fixed (and why)

- `niko2/native` keeps its number-only arithmetic: `yes * 2` raises
  `I need numbers for that operation.` while the VM computes `2`
  (Python `True * 2`). That predates Alpha 30 and is out of scope
  here; the test suite simply avoids boolean arithmetic (one case was
  rewritten to this form during development after the differential
  caught the divergence — the mechanism doing its job).
- Runtime errors still don't carry line numbers in `niko2 run` output
  (only check-time errors do). The test asserts the honest boundary:
  the taken right operand's error fires (nonzero exit, message
  present); the skipped right operand's error can never fire; a
  check-time error in the right operand reports its own
  `line 2, column 13` caret.

## Tests

`tests/test_shortcircuit.py` — 3-way VM/WASM/native differential:
truth tables per type under Niko 1's rule; skipped side never
evaluates (side-effect counters); chained `a and b and c` / mixed
`a and b or c` precedence; use in `if`/`while`/`say`/call args; the
SSG guard idiom; error line numbers for both operands. Niko 1:
extend `tests/cases/logic.niko` (+ `.out`) — it only covers boolean
operands today; add value-semantics and short-circuit cases.
