# Niko 2.0 — Alpha 15 release notes (2026-09-21)

**Bugfix release: `skip` in loops works in WASM.** A WASM codegen bug made
`skip` inside a `for` or `repeat` loop hang node forever — the compiled
code jumped back to the loop head without advancing the index, so the same
iteration repeated endlessly. `skip` inside a `match` arm inside a loop was
the visible symptom (it also hung as a bare statement). `while` was never
affected.

```niko
for each xs in [[1], [], [2, 3], []]:
    match xs:
        when []:
            skip        # used to hang node; now skips to the next item
        otherwise:
            say xs
```

The fix: `skip` now emits the loop-index increment before branching back,
exactly like the VM's continue. `stop` was verified correct already.

**Also:** `stdlib/lists.niko`'s `flatten` drops its no-op workaround
(`set nothing_to_add to yes`) and uses the clean `when []: skip` arm; the
known-limits entry for this bug is removed. Regression coverage:
`tests/test_loop_control.py` — 11 cases (skip/stop in match arms in
for/while/repeat, nested loops, guards + skip), each run 3-way differential
(VM/WASM/native, byte-identical) with timeouts so a regression can never
hang the suite.

Full suite green: 26 Niko 1 cases, 15 Niko 2 cases + nikoir round trip, all
`tests/test_*.py`.
