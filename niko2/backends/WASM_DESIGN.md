# Niko WebAssembly Backend — Design (Alpha 8)

The WASM backend compiles a **checked Niko AST** straight to a WebAssembly
binary. No Python is involved at run time: the `.wasm` module runs under any
WASM host (we ship a Node.js shim, `wasm_host.cjs`).

This document is also the seed of the **IR/backend interface** for Alpha 9:
every backend implements `niko2.backends.Backend` (`compile(tree) -> bytes`).

## Value model

Niko is dynamically typed; WASM is not. Every Niko value is a **boxed heap
object** referenced by an `i32` pointer. The object layout is:

```
offset 0: tag : i32
offset 4: payload (tag-dependent), 4-byte aligned
```

| tag | Niko value | payload |
|-----|------------|---------|
| 0 | nothing | — (single static instance) |
| 1 | number | f64 (all Niko numbers are f64) |
| 2 | text | ptr:i32, len:i32 (UTF-8 bytes) |
| 3 | yes / no | i32 0/1 (static instances) |
| 4 | list | ptr:i32, len:i32, cap:i32 (array of i32 value-pointers) |
| 5 | record | ptr:i32, len:i32 (array of key-text-ptr, value-ptr pairs) |
| 6 | result | is_ok:i32, payload:i32 (value ptr, or message text ptr) |
| 7 | function | table_idx:i32, name_ptr:i32 (text), nparams:i32, env_ptr:i32 |

Rationale: one uniform representation keeps the compiler simple and makes
host interop trivial (everything is an `i32`). Arithmetic pays a boxing
cost — acceptable for Alpha 8; a future unboxing pass can specialize
number-heavy code.

## Linear memory layout

```
[0x0000 .. 0x1000)   host scratch region (niko_fmt_num / niko_input write here)
[0x1000 .. heap)     static data (text literals, static structs, panic messages)
[heap   .. end)      bump-allocated heap (global $heap, 4-byte aligned)
```

Memory starts at 64 pages (4 MiB). `$alloc` grows memory on demand and calls
the imported `niko_panic` if growth fails.

## Host imports (`niko` module)

Things WASM cannot do alone are imported; the Node shim implements them:

| import | signature | purpose |
|--------|-----------|---------|
| niko_panic | (i32,i32) -> () | runtime error: print message, exit 1 |
| niko_print | (i32,i32) -> () | write bytes to stdout |
| niko_fmt_num | (f64) -> i32 | format f64 Niko-style into scratch, return len |
| niko_input | (i32,i32) -> (i32,i32) | print prompt, read a stdin line, return (ptr,len) |
| niko_parse_num | (i32,i32) -> f64 | parse text as number, NaN on failure |
| niko_random_i32 | (i32,i32) -> i32 | inclusive random int (Python random.randint) |
| niko_today | () -> (i32,i32) | ISO date text, return (ptr,len) |
| niko_now | () -> (i32,i32) | ISO datetime text, return (ptr,len) |
| niko_sleep | (f64) -> () | sleep seconds |

Number formatting deliberately lives in the host: correct float formatting
is a large algorithm, and the host already speaks the language's `fmt`
semantics. The WASM side owns everything else.

## Compilation strategy

The backend consumes the **checked AST** (same input as `compile_ast`), not
the flat bytecode: structured statements (`if`, `while`, `for`, `repeat`)
map directly onto WASM's structured control flow (`block`/`loop`/`if`),
which avoids recovering control flow from flat jumps.

- Module-level `set` → WASM mutable globals.
- Function params and function-level `set` → WASM locals.
- Functions → WASM functions with the uniform signature
  `(param env_ptr i32, nargs i32, args_ptr i32) (result i32)`; every call
  site goes through one `funcref` table + `call_indirect`. See
  "Closures and first-class functions (Alpha 10)" below.
- `match` is desugared exactly like `compiler.py` does (subject evaluated
  once, patterns tested in order, bindings via `unwrap`/`error_message`).
- `say` converts each value with `$to_text` (recursive, in WASM), joins
  with spaces, prints with a trailing newline — matching the VM.

## Behavioral parity rules

- Error messages raised directly as `NikoRuntimeError` in the VM
  (`You cannot divide by zero.`, `I can't pick from an empty list.`, …)
  are reproduced **exactly**.
- Errors the VM produces by wrapping a Python exception are reproduced as
  `Line N: <message>` with an equivalent message.
- `%` uses Python/Floored semantics (`-7 % 3 == 2`), not C `fmod`.
- List indexing is 1-based; text indexing mirrors the VM (0-based).
- `for each` over `a..b` works because `niko_range` builds a real list.

## Known Alpha 8 limits (see KNOWN_LIMITATIONS.md)

- Numbers are f64: ints beyond 2^53 lose precision; `4.0` and `4` are the
  same value (affects `join`'s Python-`str` formatting corner only).
- Text case ops (`upper`/`lower`) are ASCII-only; `for each` over text
  iterates bytes.
- No `use` imports (single file), no file I/O builtins, no method-call
  syntax (`x.upper()` — use `upper(x)`).
- `today`/`now` formatting may differ from the VM in sub-second digits.

## Closures and first-class functions (Alpha 10)

Nested `to` definitions capture enclosing function locals **by reference**,
driven by the shared analysis in `niko2/closures.py`
(`analyze_closures(tree)` → `ClosureInfo` per function: `captures`,
`boxes`, `qualname`).

- **Cells.** A captured variable is a heap cell: 8 bytes holding one
  boxed-value pointer. Sharing the cell pointer *is* capture by reference.
  Helpers: `$box_new(v)`, `$make_fn(table_idx, name_ptr, nparams, env_ptr)`.
- **Boxed locals.** In codegen, a local in `boxes(F)` is a WASM local
  holding the *cell pointer*: load = deref, store = write through. At
  function entry one cell is allocated per boxed name — params are boxed
  from the args array, assigned-locals start as `nothing` — so every call
  gets fresh boxes. Assigning to a *captured* (non-local) name writes
  through the box in the function's env array, exactly like the VM whose
  frame env holds the cells.
- **Environments.** `env_ptr` points at `ncaptures × 4` heap bytes holding
  cell pointers, in `captures(F)` (sorted) order. A nested `to` statement
  allocates the env array, fills it with the current cells — from the
  enclosing function's boxed locals, or from its own env array for
  pass-through names — builds the function value, and stores it to the
  name. Loop bodies reuse the entry-allocated cells, so closures created
  in a loop share the variable (Python semantics), matching the VM.
- **Calls.** `$call_fn(line, fn, nargs, args_ptr)` checks the tag
  (`I can't call <v> as a function.`, value formatted with `$to_text`)
  and the arity (`<name> expected <p> arguments, got <n>.`, the simple
  source name), then `call_indirect`s through the single funcref table
  with `(env_ptr, nargs, args_ptr)`. Builtins keep their direct calls —
  the checker rejects builtins as values before the backend runs.
- **Module functions.** At the start of `main`, one function value per
  top-level function (empty env) is materialized into its global slot, so
  named calls, `set f to add`, recursion, and mutual recursion all resolve
  through the slots — the same upfront installation the VM's `execute()`
  does.
- **Printing.** `$to_text` renders tag 7 as `function "name"` (simple
  source name, never the qualname); `fmt_nested` inherits it via
  `$to_text`. Function equality is pointer identity, like the VM.
