# Alpha 14 Design — Niko 2 Standard Library

## Goal
A bundled, useful standard library for Niko 2, written in Niko 2 itself,
importable on every backend with identical semantics.

## Modules (all pure Niko, in `niko2/stdlib/`)

| Module | Import | Functions |
|---|---|---|
| text | `import "stdlib/text.niko" as text` | title, capitalize, pad_left, pad_right, repeat, slugify, words, lines, truncate, is_blank, reverse_text, count_words |
| math | `import "stdlib/math.niko" as math` | clamp, lerp, gcd, is_prime, factorial, median, sign, is_even, is_odd, digits |
| lists | `import "stdlib/lists.niko" as lists` | chunk, zip, flatten, take, drop, pluck, group_by, partition, frequencies |
| records | `import "stdlib/records.niko" as records` | record_keys, merge, pick_keys, omit, get_or, invert, map_values |

Deliberately NOT duplicated: anything that is already a builtin
(`upper`, `split`, `join`, `sorted`, `unique`, `sum`, `average`, `max`, ...).
The stdlib builds on the builtins; it does not shadow them.

No new builtins were needed. Everything is expressed with existing
syntax (loops, closures, match patterns, first-class functions), so all
three backends get the stdlib for free through the Alpha 13 desugar.

## Determinism rule
Stdlib code must be 100% deterministic and backend-portable:
no `random_int`/`pick`, no `today`/`now`/`sleep`, no file I/O
(file builtins don't exist on WASM). Preconditions that pure Niko cannot
enforce (e.g. `chunk` needs `n >= 1`) are documented in the doc comment,
not guarded.

## Packaging: `niko2/stdlib.py` -> `niko2/stdlib/` package
`niko2/stdlib.py` (the Alpha 5 builtin registry for legacy `use`) became
the package `niko2/stdlib/__init__.py` with an identical public API
(`module_names`, `module_symbols`, `CORE_MODULES`); the only change is
`from .runtime` -> `from ..runtime`. The `.niko` sources live beside it.
`pyproject.toml` gains `[tool.setuptools.package-data]` so the `.niko`
files ship in wheels. The `git archive`-based release zip includes them
as ordinary tracked files.

## Search path (extends Alpha 13)
`resolve_import` in `niko2/modules.py` now searches, in order:

1. the importing file's directory,
2. each `NIKO_PATH` directory (env var, `os.pathsep`-separated; only
   existing directories are used),
3. the bundled standard library — only for paths starting with
   `stdlib/` (e.g. `import "stdlib/text.niko" as text`); the `stdlib/`
   prefix is stripped and the remainder is looked up under
   `niko2/stdlib/`,
4. the current working directory.

Consequences:
- A `stdlib/` tree next to the importing file (or on `NIKO_PATH`)
  shadows the bundled one — useful for vendoring or experimenting.
- Bare `import "text.niko"` does NOT find the stdlib; the `stdlib/`
  prefix is required. Explicit beats magical.
- Because modules are desugared to one Program before any backend runs,
  WASM and native binaries bundle the stdlib source at compile time.
  There is no runtime file lookup on those backends.

`NIKO_PATH` placement (before the bundled stdlib, after the importing
file's dir) means: local first, explicit override second, bundled
default third, cwd last.

## Typechecking
`check_units` typechecks each module standalone; an `import` binds its
alias to `map`. Stdlib modules must pass `niko2 check` cleanly — the
test suite asserts this for every `niko2/stdlib/*.niko` file.

## Docs: doc comments -> STDLIB.md
Each stdlib file carries structured `#` comments:

```
# stdlib/text -- everyday text helpers.
#
# Import with:
#   import "stdlib/text.niko" as text

# title(s: text) -> text
# Capitalize the first letter of every word; lowercase the rest.
# example: text.title("hello WORLD") -> Hello World
to title with s: text -> text:
```

`STDLIB.md` (the user-facing library reference) is generated from these
comments by `render_stdlib_docs()` in `tests/test_stdlib2.py`; the test
fails if the checked-in `STDLIB.md` is stale. Regenerate with
`python3 tests/test_stdlib2.py --write-docs`.

## What's NOT in this alpha
- Package manager / registry (a future alpha; noted in NEXT_STEPS).
- Cross-file LSP navigation (LSP stays single-file; known limit).
- `use "text"` (legacy builtin modules) is untouched and unrelated to
  `import "stdlib/text.niko"`.

## As built: builtin calls are now direct on the VM (CALL_BUILTIN)
Verifying the stdlib exposed a real backend divergence. `lists.group_by`
calls the `text` builtin internally; when the entry program does
`import "stdlib/text.niko" as text`, the VM resolved the bare call
`text(...)` globals-first and hit the module record ("I can't call
{...} as a function"), while WASM/native compiled the same call straight
to the builtin. The checker (Alpha 10) also says a builtin name in direct
call position always means the builtin — the VM was the odd one out.

Fix: `niko2/compiler.py` now emits a `CALL_BUILTIN (name, argc)` opcode
for `CallExpr(fn=NameExpr(b))` with `b` in `BUILTIN_NAMES`, and
`niko2/vm.py` executes it as a direct builtin call. Rule, now identical
on all three backends and matching the checker: **a builtin name in
direct call position always invokes the builtin; a user binding that
shadows the name (variable, `to`, or import alias) is invisible there.**
Shadowing still works as a value (`set text to "shadow"` / `say text`)
and for attribute access (`text.title(...)`). `tests/test_closures.py`
grew an assertion pinning the rule; `RELEASE_NOTES_ALPHA10.md`'s
"shadowing still works" line was narrowed accordingly. `pi()` raises
`'pi' is a value, not a function.` (same as WASM).
