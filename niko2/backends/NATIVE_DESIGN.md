# Niko Native Backend — Design (Alpha 9 + 10)

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
    int32_t tag;   // 0 nothing, 1 number, 2 text, 3 yesno, 4 list, 5 record,
                   // 6 result, 7 function
    union {
        double num;
        int32_t yn;
        struct { char *data; int32_t len; } text;          // UTF-8, not NUL-terminated
        struct { NVal **items; int32_t len, cap; } list;
        struct { char **keys; int32_t *klen; NVal **vals; int32_t len, cap; } rec;
        struct { int32_t ok; NVal *val; } res;             // error val is a text
        struct { int32_t func_id;                          // niko_call_dispatch id
                 char *name; int32_t nlen;                // simple source name
                 NVal *env; } fn;                         // list of cell values
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

One C function per Niko function (uniform signature), plus `main`:

```c
static NVal *niko_fn_add_1(NVal *env, int nargs, NVal **args) { ...; return nval_nothing(); }
int main(void) { ...; return 0; }
```

- **Expressions** produce a C expression of type `NVal*`. Anything needing
  statements (calls, literals, control-adjacent temps) emits them first via
  a statement buffer; fresh temporaries `t_1, t_2, ...`.
- **Variables**: top-level `set` → file-scope `static NVal *g_name;`
  (like WASM globals). Function bodies: every assigned name (plus nested
  `to` names) is pre-scanned and declared once at function top, so a `set`
  inside an `if` branch can't break C declaration dominance. Names in
  `boxes(F)` / `captures(F)` (from `niko2/closures.py`) are cells:
  reads go through `nval_cell_get`, writes through `nval_cell_set`
  (capture by reference — see "Closures" below). Anything else assigns the
  current function scope.
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
- **Calls**: every call site goes through `niko_call(line, fn, nargs, args)`
  — the callee is evaluated to an `NVal*` (fn first, then args, like the
  VM), the runtime checks the tag and dispatches via the generated
  `niko_call_dispatch` switch. Builtins → `b_name(line, args...)` direct C
  calls. `say` → `niko_say(argc, (NVal*[]){...})`. `ask` → `niko_ask(...)`.
- **Panics** carry the Niko source line: every runtime op that can fail
  takes `int line` as its first argument.

## Closures + first-class functions (Alpha 10)

The old `_reads` / `_compute_closures` rejection pass is gone. `native.py`
now uses the shared `niko2/closures.py` analysis (`analyze_closures`), which
gives every `FunctionDef` a unique `qualname` (`add`, `outer$inner`, …), a
sorted `captures` tuple (names read from enclosing *function* scopes,
including pass-through), and a sorted `boxes` tuple (own params/locals a
nested function captures).

- **Uniform signature.** Every Niko function becomes
  `static NVal *niko_fn_<qualname>_<n>(NVal *env, int nargs, NVal **args)`.
  Arity is checked on entry with the VM's wording:
  `<name> expected <p> arguments, got <n>.`
- **Function values.** `NVal` tag 7 = `{func_id, name, env}`. `name` is the
  *simple* source name, so `say f` prints `function "add"` (and
  `fmt_nested` uses the same text inside lists/records). `env` is an `NVal`
  list of cells, one per capture, in `captures` order.
- **Cells.** A cell is a 1-element `NVal` list (`nval_cell` /
  `nval_cell_get` / `nval_cell_set`). Boxed params/locals (in `boxes(F)`)
  are C locals holding cell pointers: fresh `nval_cell(...)` per call of
  the enclosing function (params from `args[i]`, assigned-locals from
  `nothing`), so each call gets independent boxes (the counter pattern).
  A captured name is unpacked once at function entry
  (`c_x = nval_list_item(env, i)`); every read is `nval_cell_get`, every
  write is `nval_cell_set` — capture by reference, matching the VM's
  cell-aware `LOAD`/`STORE` (a name in `captures(F)` always uses the env
  cell, even if the function later assigns it).
- **`to` statements.** A nested `to` builds a fresh env list from the
  current cells (own boxes or forwarded env cells for pass-through names),
  makes the function value with `nval_function`, and stores it into the
  name's slot — through the cell when the name itself is boxed (that's how
  recursion through the closure variable works: the env holds the cell,
  so the self-reference resolves after the store). Top-level `to`
  statements store into the file-scope `g_<name>` globals.
- **Calls.** Every call site evaluates the callee to an `NVal*` (fn first,
  then args, like the VM) and emits
  `niko_call(line, fn, nargs, args)`. The runtime checks the tag — a
  non-function panics with the VM's exact text,
  `I can't call <value> as a function.` (value in `say` formatting) — then
  dispatches through the generated `niko_call_dispatch` switch on
  `func_id`. Rebinding a function name (`set f to 5`) is a plain variable
  store now; builtins keep direct C calls and are still rejected as values
  by the checker.
- **Module globals** are never boxed: nested functions read them as plain
  globals (one shared namespace, like the VM).

## Parity scope

Same language subset as the WASM backend, with two upgrades the C runtime
gets for free:

| feature | WASM (Alpha 9) | native |
|---|---|---|
| `ask` (stdin) | via host import | direct `stdin` — **supported** |
| file builtins (`read_file`, `write_file`, …) | unsupported | **supported** (libc stdio, cwd-relative) |
| closures over enclosing locals | CompileError | ✅ **supported** (Alpha 10) |
| first-class function values | CompileError | ✅ **supported** (Alpha 10) |
| method calls (`x.f(...)`) | CompileError | value call (via `nval_attr` + `niko_call`) |
| `use` modules | CompileError | CompileError (same) |

The VM's constant-pool dedup bug (`say yes` → `1`) is deliberately *not*
mirrored — native prints the correct values, like WASM.

## Testing

`tests/test_native.py` mirrors `tests/test_wasm.py`: differential
VM-vs-native cases (skipped when no C compiler is found), the 8 canonical
closure programs from `tests/test_closures.py` asserted byte-identical to
their expected output, closure extras (functions in lists/records, aliasing,
rebinding), runtime error parity (`I can't call 5 as a function.`, arity
wording, builtin-as-value rejection), plus native-only wins: file I/O
builtins and `ask` with piped stdin.
