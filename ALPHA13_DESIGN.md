# Alpha 13 Design: Modules (`import`)

## Goal
Multi-file Niko 2 programs. One module system, one namespace rule, working
identically on the VM, WASM, and native backends.

## Syntax

```
import "path/to/file.niko" as alias
```

- `import` must appear at the top level of a file (not inside a function or
  a block). The checker rejects it elsewhere: `import must be at the top of the file`.
- The path must end in `.niko`, otherwise:
  `import expects a .niko file, got "…"` (line/col point at the import line).
- `as alias` is required. The alias is a plain identifier.
- Resolution: relative to the importing file's directory first, then the
  current working directory. Missing file:
  `cannot find module "…"` (line/col point at the import line).

## Namespace model: qualified access (chosen)

`import "math.niko" as m` binds `m` to a **record of the module's exports**;
call with `m.add(2, 3)`. Qualified access was chosen over unqualified merge
because it cannot collide with the importer's names and it reads the same on
every backend. The legacy `use "…"` statement (Niko 1 compatibility,
unqualified merge) is unchanged and stays interpreter/VM-only.

## Semantics

- Importing a module runs its top-level code **exactly once** (import cache),
  even when reached through several importers or imported twice under two
  aliases. A module that says something at the top level says it once.
- **Exports**: names bound by top-level `set` and `to` statements in the
  module (direct children of the file body). Nothing else is exported.
- **Cycles**: `import cycle: a.niko -> b.niko -> a.niko`, pointing at the
  `import` line that closes the cycle.
- `use` is not allowed inside imported modules:
  `'use' is not supported inside imported modules -- use 'import "…" as …' instead`.
  (The entry file may still use `use` as before.)

## Implementation: desugar to one program

All the work happens once, in `niko2/modules.py`, before any backend sees
the code — so the VM, WASM, and native backends get modules for free:

1. `build_module_graph(entry)` parses every reachable module, resolves paths,
   and detects cycles. Result: units in dependency order, entry last.
2. `check_units(graph)` typechecks each module standalone (dependencies
   first). An `import` binds its alias to `map` in the importing module, so
   `m.add(2, 3)` checks (`AttrExpr` is `any`, as before). Errors inside a
   module carry that module's path so diagnostics render against the right
   source.
3. `desugar(graph)` produces a single `Program`:
   - Each non-entry module becomes a wrapper function
     `to __import$m0:` … `give back {name: name, …}` (`$` is not a legal Niko
     identifier character, so wrappers can never collide with user code).
   - Modules are initialized in dependency order at the top of the program:
     `set __import$m0$result to __import$m0()`.
   - Each `import "…" as a` becomes `set a to __import$mK$result`.
   - The entry body runs last, unchanged apart from the import replacements.

Why a wrapper function instead of inlining: Alpha 10's closure machinery
already gives us lexical scoping, capture-by-reference, boxed forward
references, and sibling recursion — no renaming pass is needed, and module
locals can never collide across modules. The wrapper runs exactly once, so
capture-by-reference gives every importer the same shared module state.

## Backend notes

- **VM**: the desugared tree compiles as one module; `analyze_closures`
  gives wrapper-nested functions unique qualnames (`__import$m0$add`).
  Recursion and mutual recursion inside a module work through the existing
  self-capture / pre-boxed-cell machinery.
- **WASM**: same single-tree compilation. Additionally Alpha 13 removes the
  `the WASM backend doesn't support method calls yet` rejection: `m.add(2,3)`
  now flows through the first-class callee path (`_gen_expr` + `call_fn`),
  like the native backend's `niko_call` already did.
- **Native**: same single-tree compilation; `$` in synthetic names is
  sanitized by `_ident` (`$` → `_`), keeping them unique.

## Diagnostics

`ImportErrorNiko(Diagnostic)` carries `path` (the file the error belongs to);
`cli.py` renders it against that file's source, so missing-file/cycle errors
show the caret on the right `import` line in the right file. Parse/type errors
inside imported modules are re-tagged with the module path the same way.

## LSP

Single-file behavior is preserved: the checker binds an `import` alias to
`map` so nothing breaks. Go-to-definition does not follow imports across
files (recorded in KNOWN_LIMITATIONS.md).

## Tests

`tests/test_modules.py` + fixtures under `tests/niko2_cases/modules/`:
basic import + qualified access, import cache (module body runs once),
transitive imports, cycle error, missing-file error (line/col), non-.niko
error, `use`-in-module error, relative resolution from a subdir, and a 3-way
VM/WASM/native differential (byte-identical; skips without node/cc).

## As built

Three details that emerged during implementation (existing text above is
the design as planned; this is what shipped):

- **Result slots are pre-declared.** Before the wrapper functions are
  defined, `desugar_imports` emits `set __import$mK$result to nothing`
  for every module (one per unit, in dependency order), and only then
  the `to __import$mK:` wrapper definitions and the
  `set __import$mK$result to __import$mK()` initialization calls.
  This is required because the WASM backend compiles each wrapper body at
  its `to` statement: a wrapper whose top-level code references another
  module's alias must already see that module's `$result` global in
  scope. Pre-declaring as `nothing` and assigning the real record after
  the call keeps ordering safe on all three backends. Verified in
  `niko2/modules.py` (`desugar_imports`, lines ~262–299).
- **WASM method-call support came along for free.** `m.add(2, 3)` needs
  `obj.method(...)` syntax, which the WASM backend previously rejected
  (`the WASM backend doesn't support method calls yet`). The fix routes
  method calls through the first-class callee path (`_gen_expr` +
  `call_fn`), mirroring what the native backend's `niko_call` already
  did — so the rejection is gone on WASM too, and method-call syntax
  works anywhere, not just on import aliases.
- **`import`/`as` in tooling keywords.** Both `import` and `as` were added
  to `KEYWORDS` in `niko2/lsp.py` (completion offers them in `niko2 lsp`)
  and to the keyword alternation in the VS Code TextMate grammar
  (`editors/vscode/syntaxes/*.json`), so `import` and `as` highlight as
  keywords in the editor. The formatter (`niko2/formatter.py`) and the
  LSP symbol walk (`niko2/symbols.py`) both handle `ImportStmt`
  (round-trips as `import "path" as alias`).

One parser quirk worth knowing (pre-existing, not changed by this
alpha): `to name: -> type:` (colon, no `with`, no params) parses the
function name as `name:` — so write `to name -> type:` in modules.
Single-file `niko2 lsp`/`check` diagnostics don't follow imports
(see KNOWN_LIMITATIONS.md).

## Future (not this alpha)

A stdlib search path / package manager. `import` takes only relative-or-cwd
paths today; see NEXT_STEPS.md.
