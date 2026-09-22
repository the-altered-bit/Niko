# Niko 2.0 — Alpha 23 release notes (2026-09-22)

**The red squiggles finally understand multi-file programs.** Up to
now the editor diagnostics (the thing that flags errors as you type)
only looked at the file you had open: `import "lib/math.niko" as m`
followed by `m.add(1, 2)` got a phantom "unknown name" squiggle even
though the program compiled and ran fine. Alpha 23 makes the language
server analyze the whole module graph — imports and `use`s included —
so diagnostics match what `niko2 run` actually does. Along the way
this alpha also fixes the debugger's same-line breakpoint re-fire, a
formatter quote bug on `use`, and makes `use` inside a function an
error instead of a silent no-op.

## What changed

- **Module-aware editor diagnostics.** Opening a file with `import`
  or `use` statements now runs the same graph → per-module check that
  the compiler uses. Imported names and `use`-merged names no longer
  get phantom `unknown name` squiggles; `m.nope` (an attribute the
  module doesn't export) is flagged on the attribute's line; an
  unresolvable import produces exactly one diagnostic on the import
  line (no cascade); type errors are attributed to the right file and
  line (the squiggle lands in the module file, not your entry).
  Single-file documents keep the old behavior — no disk reads at all.
  Unsaved module buffers override the on-disk version, so what you see
  is what you get even before saving.
- **Debugger: a breakpoint on a call line no longer re-fires.** The
  known quirk where stepping into a call and continuing stopped *again*
  on the same line (the post-call store carries the call's line
  number) is fixed. Loops still re-fire correctly — one stop per
  iteration — because loop iterations re-execute the same instructions.
- **Formatter:** `use "a.niko"` no longer renders as `use '"a.niko"'`.
- **`use` inside a function is now an error.** `use` may only appear at
  the top of a file (like `import`); putting one inside a `to` block
  now says `'use' is only allowed at the top of a file, not inside a
  function` with a caret, instead of being silently ignored.

## What it feels like in the editor

Open `main.niko` that does `import "lib/util.niko" as util` and calls
`util.shout("hi")` — no more red squiggles on `util.shout`. Rename a
function in `util.niko` without saving: the diagnostics follow the
unsaved buffer, so the error points at the right place immediately.
Debug a call line with the breakpoint armed: step in, step out, keep
going — the debugger stops where the breakpoint is, once, not twice.

## Known limits (not fixed this alpha)

- Editor diagnostics with modules parse + typecheck every reachable
  module on each edit: single-file documents do no disk IO
  (unchanged), but a multi-file program re-reads its modules per
  `didChange`. Caching by mtime is future work (see
  `ALPHA23_DESIGN.md`).
- Diagnostics are still line-oriented: hover and go-to-definition
  resolve to the nearest sensible line, not an exact range.

## Tests

- `tests/test_lsp.py`: 8 new Alpha 23 checks over a real multi-file
  LSP session — import aliases and `use`d names produce no phantoms;
  `m.nope` → exactly 1 diagnostic on the attribute's line; missing
  import → exactly 1 diagnostic on the import line, no cascade; entry
  type error surfaces; a module-file type error is attributed to that
  file's URI + line; fixing the module's unsaved text clears its
  diagnostic; no-op entry edits prove the in-memory override is used
  (no stale module diagnostics). The Alpha 17 case now expects the 1
  `cannot find module` diagnostic (was `[]`), and the Alpha 22 case
  expects exactly 1 `cannot find module` instead of the old phantom
  `unknown name` assertion.
- `tests/test_dap.py`: new `test_no_breakpoint_refire_on_call_line` —
  breakpoint on a call line, step in, continue → the program runs to
  `terminated` with no second stop, output correct. The old "clear the
  breakpoint before stepOut" workaround in `test_use_under_debugger` is
  removed: keeping the breakpoint armed through step-out and asserting
  reason `step` is the direct proof the re-fire is gone.
- `tests/test_formatter.py`: `use "a.niko"` / `use 'b.niko'` format to
  `use "a.niko"` / `use "b.niko"` — no doubled quotes, and the output
  re-parses.
- `tests/test_use_pipeline.py`: `use`-in-function now errors at the
  checker level (line 2) and at the CLI level (exit ≠ 0, message +
  caret).

## Docs

- `ALPHA23_DESIGN.md` (new, with "As built"), `NIKO_AI_HANDOFF.md`
  (item 17), `NEXT_STEPS.md` (Alpha 23 complete),
  `niko2/KNOWN_LIMITATIONS.md` (fixed bullets removed).
