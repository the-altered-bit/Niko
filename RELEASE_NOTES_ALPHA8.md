# Niko 2.0 — Alpha 8 Release Notes
**WebAssembly backend** · 2026-09-21

Alpha 8 adds a WebAssembly compilation target to Niko 2. `niko2 wasm`
compiles checked Niko source to a standalone `.wasm` binary that runs on
any WASM runtime via a small host shim (node.js provided).

## What's new

- **`niko2 wasm <file.niko> [-o out.wasm] [--run]`** — compile to WASM;
  `--run` executes it with node.js through the bundled host shim.
- **`niko2/backends/` package** — a `Backend` ABC (`compile(tree) -> bytes`,
  `output_extension`) plus a registry. This is the IR/backend interface
  seed that Alpha 9's native backend will build on.
- **Boxed value model** — every Niko value is a heap pointer to
  `[tag, payload...]`; tags: nothing, number (f64), text (UTF-8),
  yes/no, list, record, result.
- **Full language coverage** (minus documented limits): arithmetic,
  text ops, lists, records, results, `if/otherwise`, `for each`,
  `while`, `match/when`, functions (incl. recursion), builtins,
  `say`/`ask` (ask reads via host import).
- **Differential tests** — `tests/test_wasm.py` compiles 12 programs and
  diffs WASM stdout against VM stdout (skips if node is missing).

## Known limits (see niko2/KNOWN_LIMITATIONS.md)

- Numbers are f64; `upper`/`lower` are ASCII-only.
- No `use` imports, no file-I/O builtins, no method-call syntax.
- No closures over enclosing function locals (raises `CompileError`);
  the VM supports them, so this is a real parity gap.
- No first-class function values.
- `ask` needs the host to supply stdin; `now()`/`today()`/`random_int`
  come from the host environment.

## VM bugs found (not mirrored)

- **Constant-pool dedup** (`ir.py`): Python `True == 1` / `False == 0`,
  so `constants.index(v)` can merge `yes`→`1` and `no`→`0`, printing the
  wrong one when both appear. WASM prints the correct value.

## Files

- `niko2/backends/__init__.py`, `niko2/backends/wasm.py`,
  `niko2/backends/wasm_host.cjs`, `niko2/backends/WASM_DESIGN.md`
- `tests/test_wasm.py`
- CLI: `niko2 wasm` in `niko2/cli.py`
