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
- Functions → WASM functions `(param i32)* (result i32)`, direct `call`s.
  Recursion works. First-class function values are rejected in Alpha 8.
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
  syntax (`x.upper()` — use `upper(x)`), no closures over enclosing
  function locals, no function values.
- `today`/`now` formatting may differ from the VM in sub-second digits.
