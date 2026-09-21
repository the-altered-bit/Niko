# Niko Native Backend — Design (Alpha 9)

## Approach

The native backend compiles a **checked** Niko AST to **C source**, then
invokes the system C compiler (`cc`/`gcc`/`clang`) to produce a real native
executable. This is the classic "C as portable assembler" route:

```
.niko --parse--> AST --check--> AST --native.py--> .c --cc--> executable
```

Why C and not raw machine code / LLVM IR:

- No new toolchain dependency beyond `cc`, which ships with every Linux
  and macOS dev environment (and is one install away on Windows).
- The boxed-value runtime ports 1:1 from the validated WASM backend, so
  semantics stay identical by construction.
- Generated C is human-readable — the `--emit-c` flag keeps it for
  debugging, which raw machine code could never offer.

## Files

- `niko2/backends/niko_runtime.h` — public runtime API (value model, ops,
  builtins, panic/say/ask).
- `niko2/backends/niko_runtime.c` — the runtime itself (~2k lines of C).
  Compiled and linked with every program. Ships inside the niko2 package
  so `niko2 native` works from a zip install too.
- `niko2/backends/native.py` — `NativeBackend(Backend)`:
  - `compile_c(tree) -> str` — AST to C source (also useful for tests).
  - `compile(tree) -> bytes` — full pipeline: C to temp dir, run `cc -O2`,
    read back the executable bytes. Raises `CompileError`/`NikoRuntimeError`
    with a clear message when no C compiler is on PATH.
  - `output_extension` — `""` (native binaries have no suffix on POSIX;
    `.exe` on Windows).
- CLI: `niko2 native <file.niko> [-o out] [--run] [--emit-c]`.

## Value model

Mirrors the WASM backend exactly — every Niko value is a heap-allocated
`NVal*`:

```c
struct NVal {
    int32_t tag;   // 0 nothing, 1 number, 2 text, 3 yesno, 4 list, 5 record, 6 result
    union {
        double num;
        int32_t yn;
        struct { char *data; int32_t len; } text;          // UTF-8, not NUL-terminated
        struct { NVal **items; int32_t len, cap; } list;
        struct { char **keys; int32_t *klen; NVal **vals; int32_t len, cap; } rec;
        struct { int32_t ok; NVal *val; } res;             // error val is a text
    } u;
};
```

- Numbers are `double` (same f64 limit the WASM backend documents).
- Text is a byte buffer + length (embedded NULs impossible from Niko
  strings, but the model doesn't rely on NUL termination).
- Memory: `malloc` per value, never freed. Niko programs are short-lived;
  an arena/GC is future work, not Alpha 9.
- `yes`/`no`/`nothing` are singletons.

## Codegen (`native.py`)

One C function per Niko function, plus `main`:

```c
static NVal *niko_fn_add(NVal *p_a, NVal *p_b) { ...; return nval_nothing(); }
int main(void) { ...; return 0; }
```

- **Expressions** produce a C expression of type `NVal*`. Anything needing
  statements (calls, literals, control-adjacent temps) emits them first via
  a statement buffer; fresh temporaries `t_1, t_2, ...`.
- **Variables**: top-level `set` → file-scope `static NVal *g_name;`
  (like WASM globals). Function bodies: every assigned name is pre-scanned
  and declared once at function top (`NVal *v_name;`), so a `set` inside an
  `if` branch can't break C declaration dominance. Assignment is always to
  the *current function scope* — the VM/WASM never write through a closure,
  and neither do we.
- **Name mangling**: Niko names are `[a-z_][a-z0-9_]*` and never collide
  with the `niko_`/`nval_`/`b_` prefixes; params become `p_<name>`,
  mangled per function. C keywords can't appear as Niko names (the lexer
  reserves them), except… `name`/`value` etc. are fine.
- **Control flow** maps directly: `if/otherwise` → `if/else`,
  `while` → `while (nval_truthy(...))`, `repeat n` → counted `for`,
  `for each` → index loop over `nval_to_iter_list` (list/text/record, like
  WASM), `stop`/`skip` → `break`/`continue`, `give back` → `return`.
- **`match`** is desugared exactly like the WASM backend: subject evaluated
  once into a temp; literal/ok/err/bind patterns become if-chains using
  `nval_equals` / tag checks / `nval_unwrap`.
- **Calls**: user functions → direct C calls (forward-declared, so mutual
  recursion works). Builtins → `b_name(line, args...)`. `say` →
  `niko_say(argc, (NVal*[]){...})`. `ask` → `niko_ask(...)`.
- **Panics** carry the Niko source line: every runtime op that can fail
  takes `int line` as its first argument.

## Parity scope

Same language subset as the WASM backend, with two upgrades the C runtime
gets for free:

| feature | WASM | native |
|---|---|---|
| `ask` (stdin) | via host import | direct `stdin` — **supported** |
| file builtins (`read_file`, `write_file`, …) | unsupported | **supported** (libc stdio, cwd-relative) |
| closures over enclosing locals | CompileError | CompileError (same) |
| first-class function values | CompileError | CompileError (same) |
| method calls (`x.f(...)`) | CompileError | CompileError (same) |
| `use` modules | CompileError | CompileError (same) |

The VM's constant-pool dedup bug (`say yes` → `1`) is deliberately *not*
mirrored — native prints the correct values, like WASM.

## Testing

`tests/test_native.py` mirrors `tests/test_wasm.py`: differential
VM-vs-native cases (skipped when no C compiler is found), closure
rejection, plus native-only wins: file I/O builtins and `ask` with piped
stdin. The 12-case WASM differential list is the starting corpus.
