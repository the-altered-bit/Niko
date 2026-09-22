# Niko 2.0 — Alpha 22 release notes (2026-09-22)

**`use` is a first-class citizen now.** Previously `use "file.niko"`
was a VM-only side path bolted on outside the module system: it didn't
work on WASM or native at all, silently ignored dependency cycles, and
the debugger and language server didn't know about it. Alpha 22 routes
`use` through the same module pipeline as `import` — the graph builder,
the per-module typechecker, and the desugar-to-one-Program step — so
everything that works for imports now works for `use`, byte-identically
on all three backends.

## What changed

- **`use` works on VM, WASM, and native, byte-identically.** The old
  `CompileError("...doesn't support 'use' yet")` on the WASM/native
  backends is gone. A program with `use` compiles and runs the same on
  all three.
- **`use` semantics, in one paragraph.** `use "path/to/file.niko"`
  merges the used file's top-level names into your scope (no alias,
  unlike `import ... as`). Used files run before the entry body; every
  used file's top-level code runs exactly once, even with diamond or
  repeated `use`s. On name conflicts the *later* `use` wins, and your
  entry file's own `set`s/`to`s always win over used names. `use`
  composes transitively (a used file can `use` another file), and
  functions defined in used files — including nested closures — work
  from the entry file. A `use` cycle (`a` uses `b`, `b` uses `a`) is now
  a compile error that names the loop (`use cycle: a.niko -> b.niko ->
  a.niko`) with a caret, instead of silently recursing. Using a missing
  file, `import` inside a used file, and `use` inside an imported file
  are compile errors with carets. Builtin names (`use "math"`) stay
  no-ops everywhere, as before.
- **Debugger support.** Breakpoints inside used files verify and hit;
  stepping into a used function lands on the right file/line; stack
  frames attribute to the used file; locals (including closure
  variables from the used file) are inspectable.
- **LSP support.** Go-to-definition on a used name jumps to the
  top-level `set`/`to` in the used file; the `use` path string jumps to
  the used file itself; hover shows the signature and doc comment from
  the used file. Unresolvable uses yield null, never an error.
- **REPL.** `use` in `niko2 repl` now goes through the shared pipeline
  (the old separate loader is gone); used names stay visible across
  chunks and init-once semantics hold.

## Known limits (not fixed this alpha)

- Editor *diagnostics* (the red squiggles) still flag used names as
  unknown — the LSP's live checker doesn't know `use`-merged names yet
  (same pre-existing pattern as `import` aliases). Go-to-definition and
  hover work anyway.
- `niko2 format` renders `use "a.niko"` as `use '"a.niko"'` (doubled
  quotes) — a pre-existing formatter quirk.
- `use` inside a function body keeps its old behavior (silently
  ignored on the VM).

## Tests

- `tests/test_use_pipeline.py` (new, 13 checks): 3-way differential
  (VM/WASM/native byte-identical) for basic use, transitive use,
  diamond init-once, conflict resolution, nested functions in used
  files, and builtin `use "math"`; builtin-wins-over-file; error cases
  with carets (`use` cycle incl. self-cycle, missing file, `import`
  inside a used file, `use` inside an imported module); typecheck-error
  attribution to the used file/line.
- `tests/test_dap.py`: new `test_use_under_debugger` — breakpoint
  inside a used file verifies + hits with the right file/line, step
  into a used function, step out back to the entry, closure locals
  inspectable, used-file breakpoint fires on a later call.
- `tests/test_lsp.py`: go-to-definition on a used name → the used
  file's `set`/`to`, on the `use` path string → the used file, entry's
  own `set` wins over the used file's, missing use → null; hover shows
  signature + doc comment; the diagnostics gap is asserted as current
  behavior.
- `tests/test_repl.py`: new `use-in-repl` case — used names visible
  across chunks, top-level `say` runs exactly once.

## Docs

- `ALPHA22_DESIGN.md` (new, with "As built"), `NIKO_AI_HANDOFF.md`
  (item 16), `NEXT_STEPS.md` (Alpha 22 complete), 
  `niko2/KNOWN_LIMITATIONS.md` (fixed bullets removed; the LSP
  diagnostics gap added).
