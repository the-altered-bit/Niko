# Release notes — Alpha 37: VM `stop` loop-iterator fix

The Alpha 36 fuzzer found a genuine control-flow bug in the VM: `stop`
out of a `repeat`/`for each` loop never popped the loop's iterator off
the VM's iterator stack. Nested inside another `repeat`/`for each`, the
stale iterator corrupted the outer loop — the program hung forever or
silently iterated the wrong values. Alpha 37 fixes it.

```niko
repeat 1 times:
    for each n in [1, 2]:
        stop
    say "inner done"
say "done"
```

Before: the VM hung forever (WASM and native printed `done`). Now all
three backends print `done`.

## What changed

- **Compiler** (`niko2/compiler.py`): the compiler's `loop_stack` now
  records each loop's kind (`'repeat'`/`'for'`/`'while'`). When `stop`
  targets a `repeat`/`for each`, the compiler emits a new `ITER_POP`
  opcode before the break jump. `skip` is unchanged (it jumps to the
  loop head, where the live iterator is correctly advanced), and `stop`
  out of a `while` emits no pop (no iterator involved).
- **VM** (`niko2/vm.py`): the new `ITER_POP` opcode pops the frame's
  iterator stack — the same pop the `..._NEXT` ops already do on normal
  loop exhaustion, now also on the break path.
- **Fuzzer** (`niko2/fuzz.py`): the `_gen_loop_exit` restriction that
  kept `stop` out of nested iterator loops (added in Alpha 36 to dodge
  this bug) is lifted, and the generator is now biased toward producing
  more nested-loop-with-`stop`/`skip` programs. A latent generator bug
  was fixed along the way: the `iterated` set (lists an enclosing `for
  each` is iterating, which `put`/`remove` must not touch) is now a
  refcount, so a nested loop over the same list no longer clears the
  outer loop's claim.

No language semantics changed — `stop` means exactly what it meant
before; it just no longer corrupts the machine. WASM and native were
never affected (no iterator stack there) and are untouched.

## Verification

- New `tests/test_stop.py`: 31 three-way differential cases
  (VM/WASM/native byte-identical) covering `stop`/`skip` ×
  `repeat`/`for each`/`while`, all 8 nesting shapes, `stop` through
  `if` and `match` (incl. guards), triple nesting, outer-loop
  undisturbed checks, first/last-statement `stop`, empty iterations —
  plus 2 compile-error cases (`stop` inside a function nested in a
  loop is still rejected).
- Focused fuzz campaign: 3,000 cases over loop/`stop`/`skip` shapes —
  **zero VM-vs-WASM divergences on all 3,000**; the 64 failures are all
  previously documented native-backend issues (`otherwise if`
  miscompile, `ask number` prompt). No hangs, no new bugs.
- The 34 saved Alpha 36 failure repros re-run clean on the fixed VM.
- Full suite green (402 pytest + `tests/test_stop.py`'s 31 differential
  cases; 26 Niko 1; 16 Niko 2 + nikoir round trip).

See `ALPHA37_DESIGN.md` for the mechanism and the campaign details.
