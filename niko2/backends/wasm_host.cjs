// Niko WebAssembly host shim (Alpha 8).
// Provides the `niko` module imports for programs compiled with `niko2 wasm`.
// Usage: node wasm_host.js <program.wasm>
//
// The guest writes formatted numbers at SCRATCH (0x0); line input goes to
// INPUT_BUF (0x800). Both live in the 4 KiB host scratch region and are
// consumed (copied) by the guest immediately, so fixed buffers are fine.
"use strict";
const fs = require("fs");

const SCRATCH = 0x0;
const INPUT_BUF = 0x800;

let mem = null; // set after instantiation
const bytes = () => new Uint8Array(mem.buffer);
const view = () => new DataView(mem.buffer);

function readStr(ptr, len) {
  return Buffer.from(bytes().subarray(ptr, ptr + len)).toString("utf-8");
}
function writeStr(ptr, s) {
  const b = Buffer.from(s, "utf-8");
  bytes().set(b, ptr);
  return b.length;
}

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
// plain integers (the guest appends ".0" itself where Python str() needs it),
// everything else uses Python-style float repr.
function fmtNum(x) {
  if (Number.isNaN(x)) return "nan";
  if (!Number.isFinite(x)) return x < 0 ? "-inf" : "inf";
  if (Number.isInteger(x)) return BigInt(x).toString();
  return pyReprFloat(x);
}

// Niko's number(): int(text) if it looks like an int, else float(text),
// NaN when neither parses (the guest turns NaN into a failed result / retry).
function parseNum(ptr, len) {
  const s = readStr(ptr, len).trim();
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

let stdinBuf = null;
let stdinPos = 0;
function readLine() {
  if (stdinBuf === null) {
    stdinBuf = fs.readFileSync(0, "utf-8");
  }
  if (stdinPos >= stdinBuf.length) return "";
  let end = stdinBuf.indexOf("\n", stdinPos);
  if (end === -1) end = stdinBuf.length;
  let line = stdinBuf.slice(stdinPos, end);
  stdinPos = end + 1;
  if (line.endsWith("\r")) line = line.slice(0, -1);
  return line;
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

const imports = {
  niko: {
    niko_panic(ptr, len) {
      process.stderr.write(readStr(ptr, len) + "\n");
      process.exit(1);
    },
    niko_print(ptr, len) {
      process.stdout.write(readStr(ptr, len) + "\n");
    },
    niko_fmt_num(f) {
      return writeStr(SCRATCH, fmtNum(f));
    },
    niko_input(ptr, len) {
      process.stdout.write(readStr(ptr, len));
      const line = readLine();
      const n = writeStr(INPUT_BUF, line);
      return [INPUT_BUF, n];
    },
    niko_parse_num(ptr, len) {
      return parseNum(ptr, len);
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
    niko_sleep(f) {
      if (f > 0) {
        Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, Math.round(f * 1000));
      }
    },
    niko_pow(a, b) {
      return Math.pow(a, b);
    },
  },
};

const wasmPath = process.argv[2];
if (!wasmPath) {
  process.stderr.write("usage: node wasm_host.js <program.wasm>\n");
  process.exit(2);
}
const wasmModule = new WebAssembly.Module(fs.readFileSync(wasmPath));
const instance = new WebAssembly.Instance(wasmModule, imports);
mem = instance.exports.memory;
instance.exports.main();
