# Alpha 32 — LSP rename refactoring: design

`textDocument/prepareRename` + `textDocument/rename` in `niko2/lsp.py`.
Advertised as `renameProvider: {prepareProvider: true}`.

## Rename-scope rules (as built)

1. **Locals, parameters, loop variables, `ask … into` names, match-pattern
   bindings.** Renaming one renames every reference in its scope,
   including nested closures that capture it. Shadowing is honored: an
   inner binding renames only the references bound to the inner one;
   outer references with the same spelling stay untouched.
2. **Top-level `set`/`to` (exports).** Renaming one renames its
   references in the file, and extends cross-file:
   - every file doing `import "x.niko" as alias` gets its `alias.name`
     occurrences renamed (each file keeps its own alias spelling);
   - every file merging the name in with `use "x.niko"` gets its
     bare-name references renamed.
   
   Importer discovery: open documents first (unsaved buffers included),
   then a bounded read-only walk (≤500 `.niko` files, hidden dirs and
   `node_modules`/`.venv`/`.git`/etc. skipped) of the project trees of
   the definition file and the invoking file. Project root = nearest
   ancestor holding `niko.toml`, `niko.lock` or `.git`, else the file's
   own directory. Files that fail to parse are skipped. The bundled
   stdlib and the local package cache are **in-file only** (managed
   artifacts — rename never reaches across into them).
3. **Never renamed:** builtins, keywords, string contents, comments, the
   quoted path in `import "…"` / `use "…"`, record keys (`{k: v}`) and
   record fields (`rec.field`). Cursor on the attribute side of
   `alias.name` where `alias` is a live import alias renames the
   module's export (rule 2); cursor on any other attribute renames
   nothing. Renaming an **import alias** renames the alias binding
   in-file only — the module file is never touched. Renaming a bare
   name that only arrives via `use`, from the *using* file, is refused
   with a message pointing at the defining file (open that file and the
   cross-file machinery in rule 2 does the rest).
4. **New-name validation** (LSP `InvalidParams`, code −32602,
   plain-English message): must match `[A-Za-z_][A-Za-z0-9_]*`, must not
   be a keyword, must not be a builtin (`typecheck.BUILTIN_NAMES`), and
   must not collide with a visible binding — neither a same-scope
   redefinition nor a nested binding that would capture renamed
   references (checked per renamed line against the scope tree).
   `prepareRename` returns `{range, placeholder}` on something
   renameable, `null` otherwise. `rename` on a non-renameable target
   returns the error — never a silent no-op. Renaming to the same name
   is a successful no-op (`{changes: {}}`).

## Per-handler mechanics

- **Target location** (`_locate_rename_target`): word under the cursor
  is read from the *masked* line (strings/comments blanked, so string
  contents can never match). Keyword/builtin → not renameable. An
  `obj.attr` cursor position resolves through the import-alias table +
  `find_symbol` on the alias (mirroring the alias-attribute lint's
  shadowing check) to the module's export; anything else on the
  attribute side is refused. Otherwise `symbols.find_symbol` gives the
  binding: `module`-kind → alias target (in-file only); top-level
  `set`/`to` → export target; everything else → local target. All of
  this runs on the open buffer via the existing `_tree(uri)` path, so
  unsaved edits work exactly like hover/definition.
- **Occurrence scanning** (`_scan_word_occurrences`): each involved file
  is re-scanned line by line; occurrences are classified
  `plain` / `attr` / `key` (record key `k:` inside braces, brace depth
  carried across lines). A `plain` occurrence is renamed iff
  `find_symbol` on its line returns the *identical* Symbol object —
  this is what makes shadowing fall out for free. `attr` and `key`
  occurrences are never touched by local/alias renames.
- **Importers** (`_importer_attr_edits`): per alias, the regex
  `\balias\s*\.\s*(name)\b` on masked lines, gated per line by
  `find_symbol(alias) is the module symbol` (shadowed lines skipped).
- **`use`-importers** (`_use_importer_edits`): plain occurrences where
  `find_symbol` finds nothing in-file (the entry's own definitions and
  shadowing loop/pattern bindings win) and `_search_use_tree` — the
  same resolver go-to-definition uses — resolves the name to the
  definition file (later-`use`-wins and transitivity match compile
  time).
- **Collision check** (`_check_collision`): `new_name` must not already
  be bound in the target's own scope, and at every renamed line the
  innermost scope already defining `new_name` must be the target's
  scope or none.
- **Result**: `WorkspaceEdit` with `changes` (uri → sorted TextEdits),
  0-based LSP ranges.

## Cross-file decision

**Implemented** (not in-file-only). It is reliable because every
resolution step reuses the same compile-truth functions as
go-to-definition/hover: `modules.resolve_import` / `resolve_use` for
importer discovery, `_search_use_tree` for `use` resolution, and the
`find_symbol` scope tree for shadowing. Boundaries, all deliberate:

- stdlib + package cache: in-file only (managed artifacts).
- Files that fail to parse are skipped as importers (their diagnostics
  already explain why); the invoking file must parse or rename refuses.
- The on-disk walk is capped (500 files) and skips hidden/build dirs;
  `use`-resolution of transitive dependencies reads from disk, like
  go-to-definition does (the importing file itself always honors
  unsaved buffers).
- Match-pattern bindings are recorded by `symbols.py` in the enclosing
  scope with first-wins shadowing ("close enough for navigation");
  rename inherits that approximation — renaming a pattern-bound name
  that also has an earlier same-scope binding renames the earlier
  binding's references too. Documented, not fixed: changing it would
  alter hover/definition behavior.

## Validation rules (summary)

| Check | Failure message (InvalidParams) |
|---|---|
| New name not `[A-Za-z_][A-Za-z0-9_]*` | `"…" is not a valid Niko name: use letters, digits and underscores, not starting with a digit` |
| New name is a keyword | `"…" is a keyword and cannot be used as a name` |
| New name is a builtin | `"…" is a builtin and cannot be used as a name` |
| New name bound in target's scope | `"…" is already defined in that scope` |
| New name would be captured/clash | `"…" would clash with another "…" visible where it is used` |
| Cursor on keyword / builtin / string / comment / import path / record field / undefined name | `prepareRename` → `null`; `rename` → specific plain-English error |
| Bare `use`-provided name, from the using file | `… comes from a file merged in with `use`; … open the defining file to rename it there` |

## As built

- `niko2/lsp.py`: `prepareRename`/`rename` handlers, capability
  advertisement, `-32602` mapping for `_RenameRefused`; no changes to
  diagnostics/completion/hover/definition/formatting paths.
- `tests/test_lsp_rename.py`: 26 scripted stdio tests, all asserting
  exact edit ranges and/or full resulting text — locals, params, loop
  vars, `ask` bindings, shadowing (both directions), top-level function
  + call sites, alias rename (in-file only), all refusal classes,
  `prepareRename` range/null, cross-file export rename from an importer
  (two importers, different aliases) and from the definition file,
  on-disk-only importer discovery via the project walk, `use`-importer
  bare-name propagation, `use`-from-using-file refusal, unsaved-buffer
  rename, same-name no-op, record key/field exclusion.
- Full suite green (see final report for counts); Niko 1's 26-case
  suite untouched and green.

## Deliberately left out

- `use "name"` builtin-name uses (they name builtins — never renameable).
- Renaming *to* a name that collides only inside an importing file's
  attribute position: attribute namespaces don't collide with locals,
  so no check is needed there.
- Redo/history, rename preview, or workspace-wide (beyond-project)
  search — out of scope for this alpha.
