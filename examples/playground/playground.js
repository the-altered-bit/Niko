/* Niko 2 browser playground runtime (Alpha 29).
 *
 * Pipeline: Pyodide (CDN) -> niko2.zip (embedded base64 in niko2_bundle.js)
 * -> niko2.playground.compile_source_to_wasm(source) -> WASM bytes
 * -> WebAssembly.instantiate with the `niko` host imports below
 * -> instance.exports.main().
 *
 * The ONLY network use on this page is the Pyodide CDN script tag in
 * index.html. Everything else (compiler, stdlib, examples) is embedded.
 */
"use strict";

/* ------------------------------------------------------------------ */
/* Memory protocol (mirrors niko2/backends/wasm_host.cjs).             */
/* The guest writes formatted numbers at SCRATCH (0x0); line input,   */
/* today(), and now() write to INPUT_BUF (0x800). Both live in the    */
/* 4 KiB host scratch region and are consumed (copied) by the guest   */
/* immediately, so fixed buffers are fine.                            */
/* ------------------------------------------------------------------ */
const SCRATCH = 0x0;
const INPUT_BUF = 0x800;

let wasmMem = null; // set after instantiation, before main() runs
const memBytes = () => new Uint8Array(wasmMem.buffer);

function readStr(ptr, len) {
  return new TextDecoder().decode(memBytes().subarray(ptr, ptr + len));
}
function writeStr(ptr, s) {
  const b = new TextEncoder().encode(s);
  memBytes().set(b, ptr);
  return b.length;
}

/* ---- number formatting / parsing, ported from wasm_host.cjs ---- */

// Shortest round-trip float formatting, then re-shaped into Python repr
// style: fixed notation for -4 <= exp < 16, else e.g. 1.5e+16 / 1e-05.
function pyReprFloat(x) {
  if (Number.isNaN(x)) return "nan";
  if (x === Infinity) return "inf";
  if (x === -Infinity) return "-inf";
  if (x === 0) return "0";
  const neg = x < 0;
  let s = String(Math.abs(x));
  let exp = 0;
  const epos = s.indexOf("e");
  if (epos !== -1) {
    exp = parseInt(s.slice(epos + 1), 10);
    s = s.slice(0, epos);
  }
  const dot = s.indexOf(".");
  let digits;
  if (dot !== -1) {
    exp += -(s.length - dot - 1);
    digits = s.slice(0, dot) + s.slice(dot + 1);
  } else {
    digits = s;
  }
  digits = digits.replace(/^0+/, "") || "0";
  const decExp = exp + digits.length - 1;
  let out;
  if (decExp >= -4 && decExp < 16) {
    if (decExp >= 0) {
      out =
        digits.length > decExp + 1
          ? digits.slice(0, decExp + 1) + "." + digits.slice(decExp + 1)
          : digits + "0".repeat(decExp + 1 - digits.length);
    } else {
      out = "0." + "0".repeat(-decExp - 1) + digits;
    }
  } else {
    const e =
      decExp >= 0
        ? "e+" + String(decExp).padStart(2, "0")
        : "e-" + String(-decExp).padStart(2, "0");
    out = digits[0] + (digits.length > 1 ? "." + digits.slice(1) : "") + e;
  }
  return (neg ? "-" : "") + out;
}

// Matches the WASM backend's contract: integer-valued floats render as
// plain integers (the guest appends ".0" itself where Python str() needs
// it), everything else uses Python-style float repr.
function fmtNum(x) {
  if (Number.isNaN(x)) return "nan";
  if (!Number.isFinite(x)) return x < 0 ? "-inf" : "inf";
  if (Number.isInteger(x)) return BigInt(x).toString();
  return pyReprFloat(x);
}

// Niko's number(): int(text) if it looks like an int, else float(text),
// NaN when neither parses (the guest turns NaN into a failed result).
function parseNumStr(s) {
  s = s.trim();
  if (/^[+-]?\d+$/.test(s)) {
    const n = parseInt(s, 10);
    return Number.isSafeInteger(n) ? n : NaN;
  }
  if (/^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$/.test(s)) {
    const n = parseFloat(s);
    return Number.isNaN(n) ? NaN : n;
  }
  return NaN;
}

function todayStr() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function nowStr() {
  // Matches Python's datetime.now().isoformat(): local time, microseconds.
  // JS clocks only give milliseconds, so micros are ms * 1000.
  const d = new Date();
  const p = (n, w) => String(n).padStart(w, "0");
  return (
    `${d.getFullYear()}-${p(d.getMonth() + 1, 2)}-${p(d.getDate(), 2)}` +
    `T${p(d.getHours(), 2)}:${p(d.getMinutes(), 2)}:${p(d.getSeconds(), 2)}` +
    `.${p(d.getMilliseconds() * 1000, 6)}`
  );
}

/* ------------------------------------------------------------------ */
/* Page wiring                                                         */
/* ------------------------------------------------------------------ */
const $ = (id) => document.getElementById(id);

function setStatus(text) {
  $("status").textContent = text;
}
function clearPanes() {
  $("output").textContent = "";
  $("errors").textContent = "";
  $("errors").style.display = "none";
}
function appendOutput(line) {
  const el = $("output");
  el.textContent += line + "\n";
}
function showError(msg) {
  const el = $("errors");
  el.style.display = "block";
  el.textContent += msg + "\n";
}

/* ------------------------------------------------------------------ */
/* Pyodide bootstrap                                                   */
/* ------------------------------------------------------------------ */
const PYODIDE_INDEX = "https://cdn.jsdelivr.net/pyodide/v0.29.1/full/";

let pyCompileB64 = null; // Python fn: source -> base64(wasm bytes)

async function initPlayground() {
  try {
    setStatus("loading Pyodide…");
    const pyodide = await loadPyodide({ indexURL: PYODIDE_INDEX });

    setStatus("unpacking the Niko compiler…");
    const zipBytes = Uint8Array.from(atob(NIKO2_ZIP_B64), (c) =>
      c.charCodeAt(0)
    );
    pyodide.FS.writeFile("/niko2.zip", zipBytes);
    // Base64 round-trip for compile results avoids PyProxy bytes-conversion
    // subtleties: Python returns an ASCII string, JS decodes it.
    pyCompileB64 = pyodide.runPython(
      "import sys, zipfile, base64\n" +
        "with zipfile.ZipFile('/niko2.zip') as _z:\n" +
        "    _z.extractall('/niko2pkg')\n" +
        "sys.path.insert(0, '/niko2pkg')\n" +
        "from niko2.playground import compile_source_to_wasm\n" +
        "def _compile_b64(src):\n" +
        "    return base64.b64encode(compile_source_to_wasm(src)).decode('ascii')\n" +
        "_compile_b64\n"
    );

    setStatus("ready");
    window.__nikoReady = true;
  } catch (err) {
    const msg = String((err && err.message) || err);
    setStatus("failed to load");
    showError(
      "Could not load the playground runtime: " +
        msg +
        "\n(This page needs the Pyodide CDN; check your network connection and reload.)"
    );
    // The e2e test polls this to distinguish "CDN down" from a real failure.
    window.__nikoLoadError = msg;
  }
}

/* ------------------------------------------------------------------ */
/* The `niko` host imports. The guest declares all ten unconditionally, */
/* even for trivial programs, so all ten must be present.              */
/* Multi-value (i32,i32) returns are fine in browsers.                 */
/* ------------------------------------------------------------------ */
let panicked = false;

function nikoImports() {
  return {
    niko: {
      // A guest panic is a Niko-level error, not a page crash: show it in
      // the error pane and let main() trap afterwards (flagged so we don't
      // print a second, confusing "runtime" error for the same failure).
      niko_panic(ptr, len) {
        panicked = true;
        showError("panic: " + readStr(ptr, len));
      },
      niko_print(ptr, len) {
        appendOutput(readStr(ptr, len));
      },
      niko_fmt_num(f) {
        return writeStr(SCRATCH, fmtNum(f));
      },
      niko_input(ptr, len) {
        const answer = window.prompt(readStr(ptr, len)) ?? "";
        const n = writeStr(INPUT_BUF, answer);
        return [INPUT_BUF, n];
      },
      niko_parse_num(ptr, len) {
        return parseNumStr(readStr(ptr, len));
      },
      niko_random_i32(lo, hi) {
        return lo + Math.floor(Math.random() * (hi - lo + 1));
      },
      niko_today() {
        const n = writeStr(INPUT_BUF, todayStr());
        return [INPUT_BUF, n];
      },
      niko_now() {
        const n = writeStr(INPUT_BUF, nowStr());
        return [INPUT_BUF, n];
      },
      // Blocking sleep would freeze the tab; cap it at 2 seconds.
      // Matches the WASM contract (seconds as f64) for short naps only.
      niko_sleep(f) {
        const ms = Math.min(Math.max(0, Math.round(f * 1000)), 2000);
        const end = Date.now() + ms;
        while (Date.now() < end) {
          /* busy-wait: documented limitation */
        }
      },
      niko_pow(a, b) {
        return Math.pow(a, b);
      },
    },
  };
}

/* ------------------------------------------------------------------ */
/* Compile + run                                                       */
/* ------------------------------------------------------------------ */
function pythonErrorText(err) {
  // Pyodide raises PythonError; .type is the Python exception class name,
  // .message carries str(exception). Fall back gracefully for anything else.
  const t = err && err.type ? String(err.type) : null;
  const m = err && err.message ? String(err.message) : String(err);
  return t ? t + ": " + m : m;
}

async function runProgram() {
  if (!window.__nikoReady) {
    showError("The playground is still loading — try again in a moment.");
    return;
  }
  clearPanes();
  panicked = false;
  const src = $("editor").value;

  setStatus("compiling…");
  let wasmBytes;
  try {
    // Let the Python event loop breathe so the status line paints.
    await new Promise((r) => setTimeout(r, 0));
    const b64 = pyCompileB64(src);
    wasmBytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  } catch (err) {
    // Compile error: message + Python type name, page stays usable.
    showError(pythonErrorText(err));
    setStatus("ready");
    return;
  }

  setStatus("running…");
  try {
    await new Promise((r) => setTimeout(r, 0));
    const { instance } = await WebAssembly.instantiate(
      wasmBytes,
      nikoImports()
    );
    wasmMem = instance.exports.memory; // set BEFORE main() runs
    instance.exports.main();
  } catch (err) {
    if (!panicked) showError("runtime error: " + String((err && err.message) || err));
  }
  setStatus("ready");
}

/* ------------------------------------------------------------------ */
/* UI setup                                                            */
/* ------------------------------------------------------------------ */
function initUI() {
  const sel = $("examples");
  for (const name of Object.keys(NIKO_EXAMPLES)) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name + ".niko";
    sel.appendChild(opt);
  }
  sel.addEventListener("change", () => {
    $("editor").value = NIKO_EXAMPLES[sel.value];
    clearPanes();
  });
  // Default program: the first embedded example.
  const first = Object.keys(NIKO_EXAMPLES)[0];
  sel.value = first;
  $("editor").value = NIKO_EXAMPLES[first];

  $("run").addEventListener("click", () => {
    runProgram().catch((e) => showError("internal error: " + String(e)));
  });
  $("editor").addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      runProgram().catch((er) => showError("internal error: " + String(er)));
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  initUI();
  initPlayground();
});
