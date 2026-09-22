# Alpha 29 design: browser playground

## Problem

Niko 2's WASM backend (Alpha 8) could already compile programs to
WebAssembly, but running one required a local toolchain install. A
zero-install "try it in your browser" page is the cheapest way to let
someone touch the language — and it stress-tests the WASM backend's
host contract outside Node.

## Design

Two halves, split at a clean seam:

**Compiler side — `niko2/playground.py`.** The compiler was
file-oriented (`niko2 run` reads files). The playground needs
*source string in, WASM bytes out*, so the adapter is one pure
function, `compile_source_to_wasm(source, filename, vdir)`, that runs
parse → (module pipeline if imports/uses, else plain check) → WASM
backend, with no disk IO, no network, no global state. The virtual
`filename`/`vdir` only matter for relative import resolution. All
four diagnostic exception types keep human-readable `str(e)` so the
page can display them verbatim. Nothing about the language changed;
this is a new door into the existing pipeline.

**Page side — `examples/playground/`.** Pyodide (pinned v0.29.1, the
only network request) runs CPython in the browser. `build.sh` zips the
pure-Python `niko2/` package plus `niko2/stdlib/*.niko` into
`niko2.zip`, base64s it into the generated `niko2_bundle.js` along
with the example programs (embedded, not fetched — `fetch()` fails on
`file://` pages, and the playground must work from `file://`).
`playground.js` unpacks the zip into Pyodide's filesystem, imports
the adapter, compiles, instantiates the WASM with a JS host ported
from the `wasm_host.cjs` contract (ten `niko` imports, numbers
formatted into the 4 KiB host scratch at `0x0`, `ask` answers into the
input buffer at `0x800`), and runs `main()`. The generated bundle is
**committed** so the page is shippable with zero build tooling;
re-run `build.sh` after compiler/example changes.

The editor is a plain `<textarea>` — a deliberate v1 cut. Syntax
highlighting would need a dependency for zero pipeline value.

## Browser subset (why each)

- File builtins → **compile-time** Niko error, not a runtime stub:
  failing early keeps the error in the language's own diagnostic
  format instead of inventing a browser-filesystem fiction.
- `ask` → blocking `prompt()`: matches the WASM backend's synchronous
  input model; no async redesign of the guest.
- `sleep` → capped 2 s busy-wait: the guest can't yield, so honesty
  (a cap) beats a fake.
- No `pkg:`: no network, no registry in the browser. Stdlib imports
  work because the stdlib ships inside the embedded zip.

## As built

- `niko2/playground.py` (adapter), `tests/test_playground.py`
  (7 cases, green), `niko2/backends/WASM_DESIGN.md` corrections
  (stale "no use imports" line, missing `niko_pow` row).
- Page: `index.html`, `playground.js`, generated `niko2_bundle.js`
  (~195 KB), six examples, `e2e.mjs` headless-Chromium test over CDP.
- Verified: adapter compiles fibonacci → 12,921 WASM bytes → node +
  host shim prints `fib(20) = 6765`. Full Python suite green.
- **Cut / not verified**: the in-browser end-to-end (`e2e.mjs`) could
  not run on the build machine — the Pyodide CDN is unreachable from
  it (exit 2 = skip by design). One networked run of
  `cd examples/playground && node e2e.mjs` is still owed before
  calling the page proven. The page logic itself is a thin port of the
  already-verified node host contract, so risk is contained to the
  Pyodide bootstrap path.
- Known limit carried forward: an infinite loop hangs the tab
  (WebAssembly can't be pre-empted); documented in the playground
  README rather than solved.
