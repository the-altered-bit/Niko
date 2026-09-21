# Niko 2.0 — Alpha 9 Release Notes (2026-09-21)

## Native backend: `niko2 native`

Niko programs now compile to real native executables. The pipeline is
`.niko` → checked AST → C source → system C compiler → executable:

```
niko2 native hello.niko -o hello        # build
niko2 native hello.niko --run           # build + run
niko2 native hello.niko --emit-c        # keep the generated hello.c
```

## How it works

- `niko2/backends/native.py` (`NativeBackend`, registered in
  `get_backend`/`BACKENDS`): AST → C codegen, then invokes
  `cc -O2 -I<backends> prog.c niko_runtime.c -lm` in a temp dir.
- `niko2/backends/niko_runtime.c` / `.h`: the runtime — same boxed value
  model as the WASM backend (heap `NVal*`, tags 0–6, f64 numbers, text as
  bytes+len), all builtins implemented in C against libc.
- Design notes: `niko2/backends/NATIVE_DESIGN.md`.

## Parity with the VM

- 14 differential programs (arithmetic, text ops, comparisons, control
  flow, lists, records, functions/recursion, match, results, builtins)
  produce **byte-identical output** to the VM — `tests/test_native.py`.
- Number formatting matches the VM exactly (shortest round-trip, integral
  doubles print without `.0`, records print keys unquoted, errors print
  `error("msg")`).

## Native extras (beyond what WASM supports)

- `ask` reads from stdin directly — no host shim needed.
- File builtins work: `read_file`, `write_file`, `append_file`,
  `read_lines`, `file_exists`, `try_read_file` (cwd-relative).

## Limits (see `niko2/KNOWN_LIMITATIONS.md`)

- No `use` imports, no method-call syntax, no first-class function values.
- No closures over enclosing function locals — a clear `CompileError`
  names the variable and line (same as WASM).
- The VM's constant-pool dedup bug (yes/no vs 1/0) is *not* mirrored;
  native output is the correct one.
- Needs `cc`/`gcc`/`clang` on PATH. Runtime errors print
  `Niko error at line N: msg` to stderr, exit 1 (VM prints its own format,
  exit 0).

## Tests

- `tests/test_native.py`: differential vs VM (14 cases), closure
  rejection, native-only file-I/O + `ask` tests. Skips cleanly with no C
  compiler on PATH.
- Full suite green: 26 Niko 1 cases, 12 Niko 2 cases + `nikoir` round trip,
  all `tests/test_*.py` (incl. WASM, LSP, DAP).
