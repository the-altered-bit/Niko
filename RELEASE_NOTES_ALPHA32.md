# Release notes — Alpha 32: LSP rename refactoring

`niko2 lsp` now supports `textDocument/prepareRename` and
`textDocument/rename` — the last big missing editor operation. Rename a
local, parameter, loop variable, or top-level `set`/`to` and every
reference updates, with shadowing honored and cross-file export renames
across `import`/`use` graphs.

## What it does

- **Locals, parameters, loop variables, `ask … into` names, match-pattern
  bindings**: renaming one renames every reference in its scope,
  including nested closures that capture it. An inner binding renames
  only the references bound to the inner one — outer references with the
  same spelling stay untouched.
- **Top-level `set`/`to` (exports)**: renames references in the file, and
  extends cross-file — every file doing `import "x.niko" as alias` gets
  its `alias.name` occurrences renamed (each file keeps its own alias
  spelling), and every file merging the name in with `use "x.niko"` gets
  its bare-name references renamed. Importer discovery reads open
  documents first (unsaved buffers included), then a bounded,
  read-only walk (≤500 `.niko` files, hidden/build dirs skipped) of the
  project trees. Project root = nearest ancestor holding `niko.toml`,
  `niko.lock` or `.git`, else the file's own directory. The bundled
  stdlib and the local package cache are in-file only (managed
  artifacts — rename never reaches into them).
- **Never renamed**: builtins, keywords, string contents, comments, the
  quoted path in `import "…"` / `use "…"`, record keys (`{k: v}`) and
  record fields (`rec.field`). Renaming an **import alias** renames the
  alias binding in-file only — the module file is never touched.
  Renaming a bare name that only arrives via `use`, from the *using*
  file, is refused with a message pointing at the defining file.
- **New-name validation** (LSP `InvalidParams`, plain-English message):
  must be a legal Niko name, not a keyword, not a builtin, and must not
  collide with an existing visible binding — neither a same-scope
  redefinition nor a nested binding that would capture renamed
  references. `prepareRename` returns `{range, placeholder}` on
  something renameable, `null` otherwise; `rename` on a non-renameable
  target returns the error, never a silent no-op. Renaming to the same
  name is a successful no-op.
- Works on **unsaved buffers**, exactly like hover/definition.

## Tests

New `tests/test_lsp_rename.py`: 28 scripted stdio LSP sessions, each
asserting exact edit ranges and/or full resulting text — locals,
parameters, loop vars, `ask` bindings, shadowing (both directions),
top-level function + call sites, alias rename (in-file only), all
refusal classes (builtin, keyword, collision, string content, import
path, illegal new name), `prepareRename` range/null, cross-file export
rename from an importer and from the definition file (two importers,
different aliases), on-disk-only importer discovery, `use`-importer
bare-name propagation, `use`-from-using-file refusal, unsaved-buffer
rename, same-name no-op, record key/field exclusion.

## Known approximations

Match-pattern bindings are recorded by `niko2/symbols.py` in the
enclosing scope with first-wins shadowing ("close enough for
navigation"); rename inherits that approximation — renaming a
pattern-bound name that also has an earlier same-scope binding renames
the earlier binding's references too. Changing it would alter
hover/definition behavior, so it stays.

## Also in this alpha

- `editors/vscode/README.md` documents rename as an LSP feature.
- Full suite green (see `ALPHA32_DESIGN.md`).

## Limits (unchanged)

No extract-function, no find-references, no rename preview, no
workspace-wide search beyond the project — out of scope for this
alpha.
