# Niko 2.0 — Alpha 29: Niko 2 in the browser (playground)

Alpha 29 adds **no new syntax, no new builtins** — it takes the language
somewhere new instead. `examples/playground/` is a single-page web app
where a visitor types Niko 2 code and runs it **in the page**: the real
`niko2` compiler runs inside the browser (Pyodide), compiles the source
to WebAssembly, and the page executes it with a JavaScript host. Zero
install, zero build tooling on the reader's side.

## What changed

**`examples/playground/`** (new):

```console
$ cd examples/playground && ./build.sh   # rebuilds the shippable bundle
$ node e2e.mjs                            # headless-Chromium end-to-end test
```

- `index.html` — the page: editor, Run button, output/error panes,
  status line. No frameworks, minimal CSS.
- `playground.js` — Pyodide bootstrap, the ten `niko` host imports
  (ported from the `wasm_host.cjs` contract), compile → instantiate →
  run. `say` prints to the output pane; compile errors and guest
  panics land in the error pane; a panic never crashes the page.
- `niko2_bundle.js` — **generated** by `build.sh`: base64 of a zip of
  `niko2/**/*.py` + `niko2/stdlib/*.niko`, plus the six example
  programs embedded as text (so the page works from `file://`, where
  `fetch()` is unavailable). This generated file is committed — it is
  the shippable artifact. Re-run `build.sh` after any compiler or
  example change.
- `examples/*.niko` — hello, fibonacci, fizzbuzz, list_record,
  match_demo, text_stdlib.
- `e2e.mjs` — real headless-Chromium end-to-end test over CDP: serves
  the page, waits for the compiler to be ready, runs fibonacci
  in-page and asserts `6765` in the output pane, then checks a broken
  program surfaces a compile error. Exits 2 (skip, not pass) if the
  Pyodide CDN is unreachable.

**`niko2/playground.py`** (new): the compiler-side adapter. One pure
function — `compile_source_to_wasm(source, filename="main.niko",
vdir="/playground")` — source string in, WASM bytes out, no disk IO,
no network, no global state. Programs with `import`/`use` go through
the real module pipeline (stdlib imports resolve from the bundled
stdlib; `pkg:` against the local package cache). Compile failures
raise the usual Niko diagnostics with human-readable `str(e)`.
Covered by `tests/test_playground.py` (7 cases).

**`niko2/backends/WASM_DESIGN.md`**: corrected two stale claims found
while porting the host (added `niko_pow` to the import table;
multi-file `import`/`use` work on WASM since Alpha 22).

## Pipeline

```
index.html
  -> loads Pyodide (the ONLY network request; pinned v0.29.1)
  -> playground.js unpacks niko2_bundle.js's embedded niko2.zip into
     the Pyodide filesystem, sys.path.insert(0, extracted_dir)
  -> from niko2.playground import compile_source_to_wasm
  -> compile_source_to_wasm(source) -> WASM bytes (base64 to JS)
  -> WebAssembly.instantiate(bytes, { niko: <10 host imports> })
  -> instance.exports.main() runs; output pane fills
```

## Browser differences (documented in `examples/playground/README.md`)

- `ask` → the blocking browser `prompt()`.
- File builtins fail at **compile** time with a clean Niko error —
  the browser build has no filesystem for the guest.
- `sleep(x)` is a capped busy-wait (max 2 s).
- No `pkg:` imports (no network, no registry in the browser).
- Programs are in-memory only; an infinite loop hangs the tab
  (WebAssembly can't be pre-empted) — close/reload to recover.

## Verification status

- `tests/test_playground.py`: 7/7 green (adapter contract, error
  paths, stdlib import through the pipeline).
- Core loop verified without the browser: `compile_source_to_wasm`
  on the fibonacci example → 12,921 WASM bytes → run under node with
  the host shim → prints `fib(20) = 6765`.
- `e2e.mjs` is built and correct, but **could not run here**: this
  machine cannot reach the Pyodide CDN (exit 2 = skip). It needs one
  run on a networked machine — `cd examples/playground && node e2e.mjs`.
