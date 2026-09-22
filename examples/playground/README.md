# Niko 2 Playground (Alpha 29)

A single-page, zero-build web playground for Niko 2. Write Niko in the
editor, press **Run**: the program is compiled to WebAssembly *inside your
browser* and executed there.

## How the pipeline works

```
index.html
  -> loads Pyodide (the ONLY network request; see below)
  -> playground.js writes niko2_bundle.js's embedded niko2.zip into the
     Pyodide filesystem, extracts it with Python's zipfile, and does
     sys.path.insert(0, extracted_dir)
  -> from niko2.playground import compile_source_to_wasm
  -> compile_source_to_wasm(source) -> WASM bytes (returned to JS as base64)
  -> WebAssembly.instantiate(bytes, { niko: <10 host imports> })
  -> instance.exports.memory is captured, then instance.exports.main() runs
```

The Python adapter (`niko2/playground.py`, owned by the compiler team) is a
pure function: source string in, WASM bytes out, Python exception on any
compile failure. The page surfaces `str(err)` plus the Python exception
type name in the error pane and never leaves the page in a broken state.

The JS host (`playground.js`) implements all ten `niko`-module imports the
guest declares unconditionally, following the memory protocol documented at
the top of `niko2/backends/wasm_host.cjs` (numbers are formatted into the
4 KiB host scratch region at `0x0`; `ask`/`today`/`now` answers go to the
input buffer at `0x800` and are consumed by the guest immediately).

## Network use

Exactly one: the pinned Pyodide build

- `https://cdn.jsdelivr.net/pyodide/v0.29.1/full/pyodide.js`
  (loaded with `indexURL` set to `https://cdn.jsdelivr.net/pyodide/v0.29.1/full/`)

The compiler (`niko2.zip`), the bundled stdlib, and the example programs are
all embedded in `niko2_bundle.js` — nothing else is fetched. The page works
from `file://` or any static host.

## Files

| File | Purpose |
|---|---|
| `index.html` | The page: editor, Run button, output/error panes, status line. No frameworks, minimal CSS. |
| `playground.js` | Pyodide bootstrap, the ten `niko` host imports, compile→instantiate→run. |
| `niko2_bundle.js` | **Generated** by `build.sh` — base64 `niko2.zip` + embedded examples. Commit this; it is the shippable artifact. |
| `build.sh` | Zips `niko2/**/*.py` (no `__pycache__`) + `niko2/stdlib/*.niko` and writes `niko2_bundle.js`. Re-run after any compiler or example change. |
| `examples/*.niko` | The six example programs (also embedded into the bundle by `build.sh`). |
| `e2e.mjs` | Real headless-Chromium end-to-end test (`node e2e.mjs`). |

## Editor choice

v1 uses a plain `<textarea>`: zero dependencies, works everywhere including
`file://`, and keeps the page tiny. Syntax highlighting / CodeMirror is a
possible later upgrade, not needed to prove the pipeline.

## Supported subset (browser differences from `niko2 run`)

- `say` prints to the output pane. Compile errors and guest panics appear in
  the error pane; a panic never crashes the page.
- `ask` → the blocking browser `prompt()`. This matches the WASM backend's
  synchronous input model. (Headless browsers may auto-dismiss it; the
  program then sees an empty answer.)
- File builtins (`write_file`, `try_read_file`, `read_file`, `append_file`,
  …) fail at **compile** time with a clean Niko error — the browser build
  has no filesystem for the guest.
- `sleep(x)` is a capped busy-wait (max 2 s); it blocks the tab, so keep naps short.
- No `pkg:` imports: there is no network access and no registry in the
  browser. `import "stdlib/....niko"` works — the stdlib resolves from inside
  the embedded zip.
- Programs are in-memory only: nothing persists across reloads.
- An infinite loop will hang the tab (WebAssembly can't be pre-empted);
  close/reload to recover.

## Building

```sh
cd examples/playground
./build.sh        # regenerates niko2_bundle.js
node e2e.mjs      # real headless-Chromium test (needs network for the CDN)
```

## Testing

`node e2e.mjs` (from this directory) serves the page over HTTP, drives real
Chromium via CDP, waits for `window.__nikoReady`, runs the fibonacci
example in-page and asserts `6765` appears in the output, then checks a
broken program surfaces a compile error. Exit `2` means the Pyodide CDN was
unreachable (skip, not a pass); any other nonzero exit is a real failure.
