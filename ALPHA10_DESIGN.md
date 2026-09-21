# Alpha 10 — Closures + first-class functions: shared design

All three backends (VM, WASM, native) implement these semantics. Differential
tests require **byte-identical stdout** across backends.

## 1. Language semantics (the contract)

1. **Nested `to` definitions are legal inside function bodies.** Each execution
   of the `to` statement creates a fresh function value.
2. **Capture by reference.** A nested function may read *and write* the
   enclosing function's locals/params. Captured variables are shared boxes:
   a write through any closure (or the enclosing function) is visible to all
   holders. The counter pattern works:
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
   Each *call* of the enclosing function gets fresh boxes.
3. **First-class values.** `set f to add`, `say f(2, 3)`, passing functions as
   arguments, returning them, storing them in lists/records.
4. **Printing.** `say f` prints `function "add"` (the source name; for a nested
   function, its simple source name, e.g. `function "bump"`). Inside lists /
   `fmt_nested` the same text is used.
5. **Calling a non-function** raises a runtime error with exactly this text
   (value formatted with the normal `say` formatting):
   `I can't call 5 as a function.`
   (Statically known non-callables are still rejected by the checker with the
   existing `value is not callable` error.)
6. **Builtins are NOT values.** A bare builtin name is only legal as the direct
   target of a call. Any other use (`set f to length`, `say upper`,
   `apply(length, x)`) is a *checker* error in all backends:
   `can't use the builtin "length" as a value`
   (`pi` is exempt — it is a number value, not a function.)
7. **Arity** is checked at call time: `add expected 2 arguments, got 3.`
   (same wording as the VM uses today).
8. **Loop closures share the variable** (Python semantics): functions created in
   a loop over the same local share one box.
9. Recursion through a closure variable works (the function's own name is
   captured like any other local).

Out of scope: `use` imports in WASM/native (unchanged), method-call syntax
(unchanged), closures over *module* globals are just shared globals (no boxes
needed — module scope is one shared namespace).

## 2. Shared static analysis — new module `niko2/closures.py`

All three backends (and the VM compiler) use one analysis. Do NOT keep
per-backend copies (the native backend's `_reads`/`_compute_closures` should be
replaced by this module).

```python
@dataclass
class ClosureInfo:
    node: object            # the FunctionDef
    qualname: str           # unique module-wide key: "add", "outer$inner", "outer$inner$2" (deduped)
    nested: bool            # True unless defined at module top level
    captures: tuple[str, ...]  # names captured from enclosing *function* scopes, sorted
    boxes: tuple[str, ...]     # own params/locals that a nested function captures, sorted
    parent: object | None

def analyze_closures(tree) -> dict[int, ClosureInfo]  # keyed by id(FunctionDef)
```

Definitions (walk never crosses a `FunctionDef` boundary):
- `reads(F)`: every `NameExpr` name in F's body — **including** `CallExpr` fn
  position (`f(2)` where `f` is a variable IS a variable read) — plus
  `AugAssignStmt.name`.
- `bound(F)`: params ∪ `SetStmt`/`AskStmt`/`ForStmt` names ∪ match-pattern
  bindings (`MatchBind`/`MatchOk`/`MatchErr`) ∪ `AugAssignStmt` names ∪ nested
  `FunctionDef` names in F's body.
- `target(x, F)`: nearest enclosing function of F (including F itself) with
  `x` in `bound()`; `None` if x is module-global, a builtin, `pi`, or unknown.
- `captures(F)`: `{x in reads(F) : target(x,F) is not None and target != F}`
  **plus pass-through**: for each child C of F, every `(x, t)` captured by C
  with `t is not F` is also captured by F (F sits on the path and must forward
  the box). Process children before parents (reverse pre-order).
- `boxes(F)`: `{x in bound(F) : some descendant D of F captures x with
  target D == F}` — the backend must box these locals so the box can be shared.

Notes:
- `$match` (match desugar slot) can appear in reads/bound; harmless.
- Unknown names (checker rejects them anyway) are ignored by the analysis.
- `qualname` uses `$` as separator; `$` is not a legal Niko identifier
  character, so qualnames can never collide with user-written names.

## 3. Checker (`niko2/typecheck.py`)

- Add `BUILTIN_FUNCS = frozenset(BUILTIN_NAMES - {'pi'})`.
- In `expr()`, `CallExpr` branch: if `n.fn` is a `NameExpr` whose name is in
  `self._builtin_names`, treat as a direct builtin call (as today). Otherwise
  evaluate `n.fn` normally — and in the `NameExpr` branch, raise
  `can't use the builtin "<name>" as a value` when the name is in
  `BUILTIN_FUNCS`. (No flag threading needed: the only legal position for a
  builtin name is directly in call-target position.)
- Nothing else changes: `set f to add` already checks (`FUNCTION`), calls
  through variables already check, nested `to` already typechecks with
  enclosing scopes visible.

## 4. VM + compiler (`niko2/ir.py`, `compiler.py`, `vm.py`, `nikoir.py`, `runtime.py`, `debug.py`)

Value model: a `Cell` box class in `vm.py`:
```python
class Cell:
    __slots__ = ('value',)
    def __init__(self, v): self.value = v
```
`LOAD`/`STORE` become cell-aware (no new opcodes):
- `LOAD name`: `v = env[name]` → push `v.value` if `Cell` else `v`; else try
  the frame's globals (module env, see below); else builtin; else the existing
  `I don't know what "x"` error.
- `STORE name`: if `env[name]` is a `Cell`, set `cell.value`; else plain store
  into the current frame env (never writes through to globals — unchanged).

`Frame` gains a `globals` attribute (the module env dict; defaults to `env`).
`VM.get` becomes a method on `(frame, name)` checking `frame.env`, then
`frame.globals` (if a different dict), then builtins.

`VMFunction(code, cells, globals_env)`:
- `cells`: dict name → `Cell` (shared boxes), `{}` for top-level functions.
- `globals_env`: the module env dict at definition time.

`MAKE_FUNCTION qualname` (arg is now the qualname, not the source name):
```python
fc = self._functions[qualname]; cells = {}
for name in fc.captures:
    c = f.env.get(name)
    if not isinstance(c, Cell):
        c = Cell(c); f.env[name] = c   # wrap-or-create: later STOREs hit the box
    cells[name] = c
f.stack.append(VMFunction(fc, cells, f.globals))
```
`CALL` on a `VMFunction`: `env2 = dict(fn.cells)` (shares the boxes),
bind params plainly (`env2[p] = arg` — boxing happens lazily at
`MAKE_FUNCTION` via wrap-or-create, so no eager boxing is needed), new
`Frame(code, env2, name, constants, globals=fn.globals_env)`. Arity error keeps
the existing message. Non-function, non-callable callee:
`raise NikoRuntimeError(f"I can't call {fmt(fn)} as a function.")`
(keep calling Python callables — builtins — as today).

`execute()`: install only non-nested functions:
`env[name] = VMFunction(fc, {}, env)` keyed by source name (top-level qualname
== source name). Recursion and mutual recursion work through the globals
fallback; the old "live env dict as closure" trick goes away.

`ir.FunctionCode` gains fields (all defaulted): `qualname: str | None = None`
(`__post_init__`: default to `name`), `captures: tuple = ()`,
`nested: bool = False`.

`compiler.py`: `Compiler.compile` runs `analyze_closures(tree)` first; keeps
`self.module_functions`; the `FunctionDef` branch registers into it keyed by
qualname (instead of `self.b.functions`), emitting
`MAKE_FUNCTION qualname` + `STORE source_name`. Nested defs therefore land in
the module function table and no longer `KeyError` at runtime.

`nikoir.py`: serialize `qualname`, `captures`, `nested`; bump
`NIKOIR_FORMAT_VERSION` to `'2'`; accept `'0'`, `'1'`, `'2'` (missing fields
default as in `__post_init__`).

`runtime.py` `fmt()`: function values print as `function "name"`:
```python
_fn = getattr(v, '_niko_fn_name', None)
if _fn is not None: return f'function "{_fn}"'
```
(set `_niko_fn_name` on `vm.VMFunction` and on `runtime.Function`, the legacy
tree-walker class). Place the check before the generic `str(v)` fallthrough.

`debug.py`: unwrap `Cell` in `describe_value`/`type_of_value` and in the frame
snapshot (`dict(fr.env)` values may be `Cell`s).

## 5. WASM backend (`niko2/backends/wasm.py`)

- New value tag `TAG_FUNCTION = 7`. Function value heap layout:
  `[tag=7, func_idx:i32, name_ptr:i32 (text), nparams:i32, env_ptr:i32]`.
- **Uniform function signature.** Every Niko function becomes a WASM function
  `(param i32 env_ptr, i32 nargs, i32 args_ptr) -> i32` (returns a boxed value
  pointer). `args_ptr` points at `nargs` boxed-value pointers. Drop the
  per-arity `[I32]*n -> [I32]` signatures.
- **Indirect calls.** One `funcref` table + `call_indirect` with the single
  Niko function type. Every call site (named or through a value) becomes:
  evaluate fn → must be a function value (else trap
  `I can't call <to_text(v)> as a function.`) → check `nargs == nparams`
  (trap `<name> expected <p> arguments, got <n>.`) → `call_indirect`.
  (Keep builtin calls as today's direct host/WASM calls — builtins are not
  values; the checker rejects them as values, and `_gen_call`'s existing
  `BUILTIN_NAMES` backstop stays.)
- **Envs and boxes.** `env_ptr` points at a heap array of `ncaptures` cell
  pointers; a cell is 8 heap bytes holding one boxed-value pointer (shared by
  pointer = capture by reference). In codegen, a *boxed* local (in
  `boxes(F)`) is a WASM local holding the **cell pointer**: load = deref,
  store = write through. At function entry, allocate+init one cell per boxed
  name (params from the args array, assigned-locals as `nothing`).
  The `to` statement for a nested function emits: allocate env array, fill
  with the current cells for `captures(F)`, build the function value, store
  to the name's slot.
- Module-level functions: at the start of `main`, materialize one function
  value per top-level function (env = null/empty) into its global slot;
  named calls load the slot and go through the indirect path (uniform).
  Recursion works because the slot is initialized before any call.
- `to_text`/`fmt_nested`: add the `TAG_FUNCTION` case → `function "name"`.
- Replace `test_wasm_unsupported_closure` with positive differential closure
  tests (section 7). `ask`+closures etc. need no special handling.

## 6. Native backend (`niko2/backends/native.py`, `niko_runtime.{c,h}`)

- New `NVal` tag 7 = function: payload `{int func_id; const char *name;
  NVal *env;}`. `env` is an `NVal` list of cells; a cell is a 1-element `NVal`
  list (read with index-get 1, write with index-set 1 — reuse the existing
  list primitives).
- **Uniform C signature.** Every Niko function:
  `static NVal *niko_fn_<q>_<n>(NVal *env, int nargs, NVal **args)`.
  Params come from `args`; boxed locals (`boxes(F)`) are C locals holding
  cell `NVal*`s: load = index-get, store = index-set. Pre-declare cells for
  boxed assigned-locals (init `nothing`), like the existing `_assigned`
  pre-declaration.
- `niko_call(int line, NVal *fn, int nargs, NVal **args)` in the runtime:
  tag check → `niko_panic(line, "I can't call %s as a function.")` (format the
  value with the existing value→text helper); `switch (func_id)` dispatch;
  each function checks `nargs` → panic `<name> expected <p> arguments, got
  <n>.`
- `_gen_call`: every call site evaluates the fn expression to an `NVal*` and
  emits `niko_call(...)`. Name resolution for fn position: a name with kind
  `func` is now a **variable** holding the function value (drop the old
  `can't use the function ... as a value yet` error and the
  `cannot assign to function` restriction — rebinding a function name is a
  plain variable store). Builtins keep direct C calls; the checker rejects
  builtins as values, keep the backend backstop error.
- Top-level functions: file-scope `static NVal *g_<name>;` globals holding
  function values, initialized in `main` (env = empty list). Nested `to`
  statements: build env list from current cells for `captures(F)`, build the
  function value, store to the local slot. Replace the `_reads` /
  `_compute_closures` rejection with this — preferably by switching to the
  shared `niko2/closures.py` analysis.
- `say`/formatting of a function value → `function "name"` in C.
- Replace `test_native_unsupported_closure` with positive differential closure
  tests (section 7).

## 7. Canonical closure test programs (all backends, byte-identical stdout)

```
# T1 counter (capture-by-reference + fresh boxes per call)
to counter with start:
    set n to start
    to bump:
        set n to n + 1
        give back n
    give back bump
set c to counter(10)
say c()
say c()
set d to counter(100)
say d()
say c()
# expected: 11 12 101 13

# T2 first-class: alias, arg, return, say
to add with a, b:
    give back a + b
set f to add
say f(2, 3)
say f
to apply with g, x:
    give back g(x, 1)
say apply(add, 10)
to make_adder with n:
    to adder with x:
        give back x + n
    give back adder
set add5 to make_adder(5)
say add5(10)
say add5(20)
# expected: 5 / function "add" / 11 / 15 / 25

# T3 write through closure, read outside
to box:
    set v to 0
    to setv with x:
        set v to x
    to getv:
        give back v
    give back [setv, getv]
set pair to box()
set setter to pair[1]
set getter to pair[2]
setter(42)
say getter()
# expected: 42

# T4 deep nesting pass-through
to a:
    set x to 1
    to b:
        to c:
            set x to x + 10
            give back x
        give back c()
    give back b()
say a()
# expected: 11

# T5 recursion through closure variable + nested call by name
to outer:
    to fact with n:
        if n <= 1:
            give back 1
        give back n * fact(n - 1)
    give back fact(5)
say outer()
# expected: 120
```

Also: checker rejection `set f to length` →
`can't use the builtin "length" as a value` (via `niko2 check`, all backends
share the checker); runtime `I can't call 5 as a function.` (VM test asserts
stdout/stderr text; WASM/native assert their stderr/panic text contains it).

Caveat: the VM constant-pool dedup bug (yes/no vs 1/0) still applies — test
programs avoid mixing them.

## 8. Work split

- **Core (done first):** `niko2/closures.py` (new), `ir.py`, `typecheck.py`,
  `compiler.py`, `vm.py`, `nikoir.py`, `runtime.py` (`fmt`), `debug.py`,
  `tests/niko2_cases/closures.niko` (+`.out`), `tests/test_closures.py`
  (VM-side; the 3-way differential file lands at integration time).
- **WASM agent:** `niko2/backends/wasm.py` (+ host/shim only if needed),
  `tests/test_wasm.py` (replace `test_wasm_unsupported_closure`; add T1–T5 to
  the differential CASES), update `niko2/backends/WASM_DESIGN.md`.
- **Native agent:** `niko2/backends/native.py`, `niko2/backends/niko_runtime.c`
  / `.h`, `tests/test_native.py` (replace `test_native_unsupported_closure`;
  add T1–T5 to CASES), update the native design doc
  (`niko2/backends/NATIVE_DESIGN.md`).
- **Integration (coordinator):** `tests/test_closures.py` 3-way differential
  (VM vs WASM vs native, skipping missing node/cc), full suite green,
  `RELEASE_NOTES_ALPHA10.md`, `NIKO_AI_HANDOFF.md`, `NEXT_STEPS.md`,
  `niko2/KNOWN_LIMITATIONS.md`, commit + push, zip + README + verify.

Rules: no new user-visible syntax; keep line numbers in diagnostics; Niko 1
(`niko.py`, 26 cases) untouched and green; scratch npm only in /tmp.
