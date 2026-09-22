# Niko 2.0 — Alpha 17 release notes (2026-09-22)

**Go-to-definition follows your imports.** The language server's
`textDocument/definition` now jumps across files: put the cursor on
`name` in `alias.name` and the editor opens the module file at the
top-level `set`/`to` that defines it. Put the cursor on the path string
in `import "path/to/file.niko" as alias` and the editor opens the module
file itself.

```niko
import "lib/math.niko" as m

say m.add(1, 2)   # go-to-definition on `add` -> lib/math.niko, the `to add` line
```

## How it works

- **One resolution rule everywhere.** The LSP reuses
  `niko2/modules.py`'s `resolve_import`, so the editor resolves exactly
  what the compiler resolves: the importing file's directory →
  `NIKO_PATH` dirs → the bundled stdlib (`stdlib/…` paths) → cwd → the
  package cache (`pkg:<name>/…` via the `niko.lock` pin, else the newest
  cached version). Relative `./` imports *inside* cached packages work
  too.
- **Two jump targets.** Cursor on the quoted import path → the module file
  (first line). Cursor on the attribute in `alias.name` → the top-level
  `set` or `to` defining `name` in the module file (falling back to the
  file's first line when the module can't be parsed or doesn't define the
  name). Cursor on the alias itself still jumps to the `import` line, as
  before.
- **Unresolvable imports are quiet.** A missing file or a bad `pkg:` spec
  yields no definition (the LSP `null` result) — never an error, never a
  crash — so a half-written import line doesn't break the editor.
- **Deliberate limits:** definition still resolves to a line rather than an
  exact column range (Niko 2 AST nodes carry line numbers only); hover
  doesn't follow imports yet; one hop only (no transitive
  `alias.other.x` chains).

Tests: extended `tests/test_lsp.py` — real LSP sessions over a
multi-file fixture (definition on `m.add`/`m.tau` lands in the module file
at the right line, the import string opens the module file, a `pkg:`
import resolves through a lockfile pin, a `./` import inside the package
reaches the sibling file, a missing module yields a graceful null, and the
alias itself still jumps to the `import` line). Full suite green: 26 Niko 1
cases, 15 Niko 2 cases + nikoir round trip, all `tests/test_*.py`.
