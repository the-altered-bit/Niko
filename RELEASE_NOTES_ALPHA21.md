# Niko 2.0 — Alpha 21 release notes (2026-09-22)

**A tooling polish batch.** Alpha 21 fixes three small, long-standing
paper cuts — the `to name: -> type:` parser quirk, conditional breakpoints
in the debugger, and LSP hover following imports — and officially cuts one
item (`use` under the debugger) as needing a redesign, not a patch.

## Fixed

- **Parser: `to greet: -> text:` now parses.** A colon after the function
  name (or after the params, before the `->` return-type arrow) no longer
  becomes part of the identifier. `to greet: -> text:` defines `greet`;
  `to add with a, b: -> number:` defines `add` with params `a`, `b`.
  The fix is narrow: it only changes parse output for headers that
  previously produced *uncallable* functions (a function named `greet:`
  could never be invoked), so nothing that worked before can change
  behavior. Typed params (`n: number`) and mid-string colons are
  untouched; line numbers are preserved.

- **Conditional breakpoints.** DAP `setBreakpoints` now honors the
  `condition` field: the condition is evaluated in the paused frame's
  context (reusing the Alpha 18 `evaluate` machinery — typechecked
  against locals, run on a fresh VM with an instruction budget) and the
  debugger only stops when it's truthy. No or empty condition behaves
  exactly as before. A condition that fails to parse/typecheck/evaluate
  emits a `Niko warning: breakpoint condition "…" failed: <reason>`
  through the normal output channel and the breakpoint stops as if
  unconditional — stopping surfaces the error; silently skipping would
  be invisible. The session never dies or hangs.

- **Cross-file LSP hover.** `textDocument/hover` now follows imports the
  same way go-to-definition does (Alpha 17). Hovering `m.add` shows the
  signature and doc comment from the module file; same for `pkg:` package
  imports. Unresolvable imports yield null hover, never an error.

## Cut (with a reason)

- **`use` under the debugger is still unsupported.** Empirical check
  showed the "small fix" (pre-loading used files via `VMLoader`) would
  produce a *wrong* session, not a limited one: used files compile as
  separate modules with ambiguous `<main>`/plain qualname frames, the
  debugger can't attribute breakpoints, and entry breakpoints would
  spuriously fire. The real fix is to route `use` through the module
  pipeline with `__use$K` wrapper prefixes (reusing the Alpha 18
  attribution scheme) — one change that would fix `niko2 run`, the
  debugger, and a nested-function orphaning bug together. That's a
  future sprint, not a patch.

## Tests

- `tests/test_function_header.py` (new): parser assertions + end-to-end
  runs + VM/WASM/native differential for the colon forms.
- `tests/niko2_cases/function_colon.niko` (+`.out`): `to greet: -> text:`
  and `to add with a, b: -> number:` defined and called.
- `tests/test_dap.py`: 4 new cases — condition true stops once,
  condition false never stops, bad condition warns + stops + session
  survives, empty condition stops every iteration.
- `tests/test_lsp.py`: hover on `m.add`/`m.tau` shows signature + doc
  comment from the module file; `pkg:`-pinned hover; missing-module
  hover is null.

## Docs

- `NIKO_AI_HANDOFF.md` (item 15), `NEXT_STEPS.md` (Alpha 21 complete;
  `use`-through-module-pipeline recorded as future work),
  `niko2/KNOWN_LIMITATIONS.md` (removed the fixed bullets; the
  `use`-under-debugger bullet now points at the module-pipeline fix).
