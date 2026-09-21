# Niko 2.0 — Alpha 13 release notes (2026-09-21)

**Modules.** Multi-file Niko 2 programs: split code across `.niko` files
and pull them in with `import`. One module system, qualified access,
identical on all three backends, byte-identical.

## New syntax

```
import "lib/mathutil.niko" as m

say m.add(2, 3)
say m.factor
```

`mathutil.niko`:

```
to add with x, y:
    give back x + y
set factor to 10
```

- `import` must be at the top level of a file (not inside a `to` or a
  block); elsewhere the checker rejects it with
  `import must be at the top of the file`.
- The path must end in `.niko` and must be quoted;
  `import "notes.txt" as n` is a compile error:
  `import expects a .niko file, got "notes.txt"`. An `as alias` after the
  path is required (`expected as ALIAS after the import path`).
- Paths resolve relative to the importing file's directory first, then
  the current working directory. A missing file reports
  `cannot find module "…"` with the caret on the import line.

## Qualified access and exports

The alias binds to a **record of the module's exports** — call functions
as `m.add(2, 3)` and read values as `m.factor`. Exports are the names
bound by top-level `set` and `to` in the module; everything else stays
private. The legacy `use "…"` statement (Niko 1 compatibility,
unqualified merge) is unchanged in the entry file, but rejected inside
imported modules:
`'use' is not supported inside imported modules -- use 'import "…" as …'
instead`.

## Import cache

A module's top-level code runs **exactly once** per program, no matter
how many importers it has or how many aliases import it. A module that
says something at the top level says it once; transitive imports share
the same loaded module. Each importer's alias points at the same record,
so module state is shared, not copied.

## Cycles

An import cycle is a compile error that names the loop and points at the
`import` line that closes it:

```
import cycle: cyc1.niko -> cyc2.niko -> cyc1.niko
```

## Errors

Parse/type errors inside a module are reported **against the module
file**, with line/column carets on that file's source — importing a
broken module points at the module's own bad line, not at the import
line. Module diagnostics render on all three backends.

## Backends

All three backends (VM, WASM, native) get modules for free: `import`
desugars to a single `Program` before any backend compiles it (see
`ALPHA13_DESIGN.md`). `tests/test_modules.py` runs 7 multi-file programs
3-way differential (byte-identical across VM, WASM, native) and asserts 7
error cases on every available backend.

## Limits

- Single-file tools don't follow imports: `niko2 lsp` and `niko2 check`
  analyze one file at a time, so go-to-definition/hover don't cross the
  import boundary.
- No search path yet: paths resolve relative to the importing file, then
  cwd only — no stdlib search path or package manager.
- `import` must be at the top of the file, not inside functions or blocks.
