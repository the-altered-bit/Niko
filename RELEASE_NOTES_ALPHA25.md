# Niko 2.0 — Alpha 25: VS Code extension refresh + publish prep

The VS Code extension was packaged once as `niko-0.7.0.vsix` back in
Alpha 7 and never updated since. It predated multi-file modules,
cross-file go-to-definition/hover, module-aware diagnostics, multi-file
debugging, conditional breakpoints, `ask` under the debugger, and DAP
`evaluate`. Alpha 25 brings the extension current and prepares the one
step only a human can do: publishing to the Marketplace.

## What changed

**Extension 0.7.0 → 0.25.0** (`editors/vscode/`). The version now marks
the extension current through Alpha 25 (the old 0.7.0 tracked Alpha 7).
Publisher (`niko-lang`), id (`niko`), and engine requirement (`^1.80.0`)
are unchanged.

- **Grammar** (`syntaxes/niko.tmLanguage.json`): audited against the
  parser/checker. Added the list-mutation statements (`put`, `remove`,
  `add`, `take`), the syntactic markers `from`/`into`, the long-form
  comparison operators (`is bigger than`, `is smaller than`, `is at
  least`, `is at most`, `is in` — longest-first so `is in` isn't
  swallowed by `is`), and single-quoted strings (the lexer accepts
  `'...'`; the grammar only had double-quoted). The 48-name builtin list
  was verified programmatically against `niko2.typecheck.BUILTIN_NAMES`
  — no drift. `test-grammar.js` gained 17 token-scope cases plus a
  builtin-sync self-check and is green.
- **Packaging**: `package.json` description now sells the real feature
  set (multi-file LSP + multi-file DAP with conditional breakpoints and
  evaluate); added the `inputTimeout` launch attribute (supported by
  `niko2 debug` since Alpha 18) and `activationEvents:
  ["onLanguage:niko"]` (modern `vsce` requires it when `main` is
  present — packaging failed without it). New `niko-0.25.0.vsix`
  (committed next to the old one per repo convention; the stale
  `niko-0.7.0.vsix` is removed).
- **README**: fixed the stale "`use` imports are not loaded under the
  debugger" line (wrong since Alpha 22), accurate feature list
  (cross-file go-to-definition/hover, module-aware diagnostics,
  multi-file debugging, conditional breakpoints, evaluate), the
  `ask`-under-VS-Code caveat (stock VS Code doesn't answer the
  Niko-specific reverse `input` request, so `ask` yields `""` after the
  timeout), and updated install instructions for the new vsix filename.
- **PUBLISH.md** (new): the publish runbook — publisher creation at the
  Marketplace publisher management page, PAT with the Marketplace
  "Manage" scope, `vsce publish` vs. manual upload, and how to verify
  the listing. Publishing itself is deliberately not done here: it
  needs the user's publisher account and token.

## Verification

- `node test-grammar.js`: green (real TextMate engine).
- Scripted LSP session using the exact command the extension launches
  (`python3 -m niko2 lsp`) on a multi-file program (`import` + `use` +
  match guards/patterns): module-aware diagnostics, cross-file hover
  with inferred type + doc comment, go-to-definition on `lib.shout`
  and on the import path string, format-document — all green.
- Scripted DAP session using the exact command (`python3 -m niko2
  debug`): conditional breakpoint stopped exactly on the right
  iteration, stack frames spanned both files, evaluate returned the
  right value, and the reverse `input` request went unanswered (like
  stock VS Code) so `ask` yielded `""` after the timeout and the session
  terminated cleanly — all green.
- One clean headless VS Code run (`@vscode/test-electron` + xvfb,
  extension loaded as a dev extension): language registered, activated
  on `.niko` open, debug session started and terminated. Subsequent
  headless runs SIGSEGV'd inside the Electron binary itself (even `code
  --version` segfaults — a container issue, not the extension).
  Honest boundary: full in-IDE verification awaits a real VS Code.

## What remains (user action)

Run `editors/vscode/PUBLISH.md`. Nobody but the user can create the
publisher and token.

Full suite green: 26 Niko 1 cases, 16 Niko 2 cases + nikoir round trip,
all `tests/test_*.py`.
