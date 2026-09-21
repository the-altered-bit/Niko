# Niko 2.0 — Alpha 10 Release Notes (2026-09-21)

## Closures + first-class functions, on every backend

Nested `to` definitions now capture enclosing function locals **by
reference**, and functions are real values — on the VM, the WASM backend,
and the native backend, all byte-identical:

```
to counter with start:
    set n to start
    to bump:
        set n to n + 1
        give back n
    give back bump
set c to counter(10)
say c()   # 11
say c()   # 12
```

```
to add with a, b:
    give back a + b
set f to add        # alias
say f(2, 3)         # 5
say f               # function "add"
```

Functions can be passed as arguments, returned from functions, and stored
in lists/records (`pair[2]()`, `r["f"](1, 2)`).

## Semantics (locked)

- **Capture by reference.** The closure and the enclosing function share
  one box per captured variable; writes are visible through every holder.
  Each call of the enclosing function gets fresh boxes, so two counters
  are independent.
- **Write-through.** `set x to v` inside a nested function resolves
  lexically: if `x` isn't bound in the current function yet, the write
  goes to the enclosing function's box (this is what makes `set n to n + 1`
  work inside `bump`).
- **Loop closures share the variable** (Python semantics): closures
  created in a `for each` loop all see the loop variable's final value.
- **`say f` prints `function "add"`** (simple source name) on all backends.
- **Calling a non-function** is a runtime error: `I can't call 5 as a
  function.` Statically-known non-callables are still rejected by the
  checker (`value is not callable`).
- **Builtins are not values.** A bare builtin name is only legal as the
  direct target of a call; `set f to length` is a checker error:
  `can't use the builtin "length" as a value`. (`pi` is exempt — it's a
  number.) A user variable that shadows a builtin name still works.

## How it's implemented

- `niko2/closures.py` (new, shared): `analyze_closures(tree)` maps every
  `FunctionDef` to its `ClosureInfo` — unique `qualname` (`outer$inner`,
  deduped), `captures`, `boxes`, `nested`, `parent`. Handles deep
  pass-through (a middle function forwards boxes it doesn't use itself)
  and order-sensitive references (`set n to n + 1` reads the enclosing
  `n`).
- **VM** (`niko2/vm.py`): `Cell` boxes; `LOAD`/`STORE` are cell-aware (no
  new opcodes); `VMFunction(code, cells, globals_env)`; `MAKE_FUNCTION`
  takes the qualname and wrap-or-creates cells in the defining frame;
  top-level functions install as values with no cells (recursion resolves
  through the shared module env).
- **WASM** (`niko2/backends/wasm.py`): new `TAG_FUNCTION = 7`; function
  value = `[tag, table_idx, name_ptr, nparams, env_ptr]`; every Niko
  function gets the uniform `(env_ptr, nargs, args_ptr) -> boxed` signature
  with one `funcref` table + `call_indirect`; boxed locals hold cell
  pointers. Design notes: `niko2/backends/WASM_DESIGN.md`.
- **Native** (`niko2/backends/native.py`, `niko_runtime.c/.h`): new `NVal`
  tag 7 `{func_id, name, env}`; same uniform-signature + cells-as-1-element-
  `NVal`-lists approach; `niko_call(line, fn, ...)` dispatches with tag
  check + arity panic. Design notes: `niko2/backends/NATIVE_DESIGN.md`.

## Testing

- `tests/niko2_cases/closures.niko` (+`.out`): the canonical programs in
  the VM regression suite (13 niko2 cases total, 0 failures; also covers
  `.nikoir` round trip with nested functions).
- `tests/test_closures.py`: VM-side assertions — the 8 canonical programs,
  checker rejections (builtin-as-value, shadowing still legal), runtime
  error messages, closure-analysis units, `.nikoir` round trip, and a
  **3-way differential** (VM vs WASM vs native, byte-identical; skips
  gracefully without node/cc).
- `tests/test_wasm.py` / `tests/test_native.py`: each runs the same 8
  canonical programs through its backend against the expected output.
- Full suite green: 26 niko1 cases, 13 niko2 cases + nikoir round trip,
  all `test_*.py` (closures, results/match, wasm, native, stdlib,
  generics, diagnostics, formatter, project deps, LSP, DAP).

## Still true

- The VM constant-pool dedup bug (`yes`/`1`, `no`/`0`) is unchanged and
  still not mirrored by the backends.
- `nikoir` format bumped to `'2'` (loads `'0'`/`'1'`/`'2'`).
