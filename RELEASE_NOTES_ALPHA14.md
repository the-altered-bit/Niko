# Niko 2.0 — Alpha 14 release notes (2026-09-21)

**Standard library.** Four pure-Niko modules bundled with Niko 2, usable
from any program on any backend — VM, WASM, and native, byte-identical:

```
import "stdlib/text.niko" as text
import "stdlib/math.niko" as math
import "stdlib/lists.niko" as lists
import "stdlib/records.niko" as records

say text.title("hello WORLD")      # Hello World
say math.gcd(12, 18)               # 6
say lists.chunk([1, 2, 3, 4, 5], 2) # [[1, 2], [3, 4], [5]]
say records.merge({x: 1}, {y: 2})   # {x: 1, y: 2}
```

## The library

**stdlib/text** — `title`, `capitalize`, `pad_left`, `pad_right`,
`repeat`, `slugify`, `words`, `lines`, `truncate`, `is_blank`,
`reverse_text`, `count_words`.

**stdlib/math** — `clamp`, `lerp`, `gcd`, `is_prime`, `factorial`,
`median`, `sign`, `is_even`, `is_odd`, `digits`.

**stdlib/lists** — `chunk`, `zip`, `flatten`, `take`, `drop`, `pluck`,
`group_by`, `partition`, `frequencies`.

**stdlib/records** — `record_keys`, `merge`, `pick_keys`, `omit`, `get_or`,
`invert`, `map_values`.

The full reference — every signature, a description, and a runnable
example — is `STDLIB.md`, generated from the doc comments in the modules
themselves (so it can't go stale: `tests/test_stdlib2.py` fails if it
does; regenerate with `python3 tests/test_stdlib2.py --write-docs`).

Deliberate non-goals: anything that is already a builtin (`upper`,
`split`, `sorted`, `unique`, `sum`, `average`, `max`, …) is not duplicated
— the stdlib builds on the builtins, with one pragmatic exception:
`records.record_keys(r)` is a `keys()` that can't be shadowed (a parameter
named `keys`, as in `omit(r, keys)`, shadows the builtin inside that
function's body, so the helper exists for exactly that case). No new
builtins were added for this
alpha; everything is plain Niko 2 (loops, closures, match patterns,
first-class functions), which is why all three backends get it for free.

## Search path

`import` now resolves in a documented order:

1. the importing file's directory,
2. each `NIKO_PATH` directory (env var, `:`-separated),
3. the bundled standard library — for paths starting with `stdlib/`,
4. the current working directory.

A `stdlib/` folder next to your file or on `NIKO_PATH` shadows the
bundled one, so you can vendor or experiment. Bare
`import "text.niko"` does *not* find the stdlib — the `stdlib/` prefix
is required.

Because modules desugar to one program before any backend compiles them
(Alpha 13), WASM and native binaries bundle the stdlib source at compile
time; there is no file lookup at runtime on those backends.

## Determinism rule

Stdlib code is 100% deterministic and backend-portable: no randomness,
no clock, no file I/O (file builtins don't exist on WASM). Preconditions
pure Niko can't enforce (e.g. `chunk` needs `n >= 1`) are documented on
the function, not guarded.

## Tests

`tests/test_stdlib2.py`:

- every `niko2/stdlib/*.niko` passes `niko2 check`, has a doc comment
  (with a runnable example) for every function, and keeps a clean top
  level (no stray `say`/`set`);
- **every doc example is executed** — the examples are the test corpus.
  Documented output must equal actual `say` output, or the suite fails;
- the whole example corpus runs 3-way differential (VM/WASM/native,
  byte-identical) and twice for determinism;
- error cases: `import "stdlib/nope.niko"` →
  `cannot find module "stdlib/nope.niko"` with the caret on the import
  line; `NIKO_PATH` shadowing works.

## Limits

- No package manager or registry — that's a future alpha.
- `niko2 lsp` stays single-file: completion/hover don't know about
  stdlib functions yet.
- The legacy `use "text"` builtin-module registry (`niko2/stdlib/`
  `__init__.py`, Alpha 5) is unrelated to `import "stdlib/text.niko"`;
  both keep working.

## Also fixed: builtin calls are direct on the VM
Verifying the stdlib caught a real backend divergence: `lists.group_by`
calls the `text` builtin, but with `import "stdlib/text.niko" as text`
the VM resolved that bare call to the import alias and crashed, while
WASM/native (and the checker's Alpha 10 rule) always send a builtin name
in call position straight to the builtin. The VM now emits a
`CALL_BUILTIN` opcode for that shape, so the rule is identical on all
three backends: **a builtin name in direct call position always invokes
the builtin** — a shadowing variable, `to`, or import alias is invisible
there (shadowing still works as a value and for `m.attr` access).
`tests/test_closures.py` pins the rule.
