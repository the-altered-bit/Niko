# Niko 2.0 — Alpha 31: `json` stdlib module (parse/stringify)

Alpha 31 adds a fifth standard-library module: `niko2/stdlib/json.niko`
— pure Niko, no new syntax, no new builtins, no backend changes.
`import "stdlib/json.niko" as json` gives you `json.parse(text)` and
`json.stringify(value)`, byte-identical on VM/WASM/native (with the
documented divergences below).

## The API

- `json.parse(text) -> any` — parse one JSON document. Objects become
  records (duplicate keys: last value wins), arrays become lists,
  strings become text, numbers become numbers, `true`/`false`/`null`
  become `yes`/`no`/`nothing`. Leading/trailing whitespace allowed.
  ```niko
  import "stdlib/json.niko" as json
  say json.parse("{\"name\": \"Ada\", \"n\": 1.5}")
  say json.parse("[1, \"two\", true, null]")
  ```
- `json.stringify(value) -> text` — compact JSON (`{"b":1,"a":[true,null]}`),
  record keys in insertion order (the order `parse` produces), text
  quoted and escaped, numbers via `text()`, `yes`/`no`/`nothing` as
  `true`/`false`/`null`. Anything else (e.g. a function) is a
  `json stringify error`.
- 22 `_`-prefixed private helpers (recursive-descent parser over a
  `{pos}` cursor record, escape/unicode/number decoders, stringify
  walkers) — all doc-commented like the rest of the stdlib, so they
  show up in `STDLIB.md`.

## Errors

Parse failures raise `json parse error at character N: <msg>` with
1-based positions, via `unwrap(error(...))` so they surface natively
on all three backends (VM: `Niko error in file:`; native: `Niko error:`;
WASM: the guest panic message). Examples: `{"a": 1` →
`at character 8: unterminated object`; `[1,]` →
`at character 4: unexpected character "]"`; `01` →
`at character 2: unexpected trailing text`.

## Known limitations (documented, tested)

1. **`\uXXXX` escapes above U+007F are a parse error** naming the
   position (`\u0041`–`\u007f` decode fine). On VM/native, write such
   characters as raw UTF-8 instead; on WASM, parsing a JSON string
   containing raw multibyte UTF-8 fails — a WASM backend bug
   (`while yes:` loops miscompile string indexing on multibyte text;
   see `niko2/KNOWN_LIMITATIONS.md`). `stringify` of multibyte text
   works on all three backends.
2. **Numbers with magnitude ≥ 1e15 are rejected** with
   `number out of range` — the backends can't agree (VM keeps integers
   exact, WASM/native use f64), so the module rejects uniformly.
   Carry bigger integers as text.
3. **Integer-valued floats render `1.0` on the VM but `1` on
   WASM/native** — each backend's own `text()` rule; both are valid
   JSON for the same value.

## Pure-Niko workarounds (no backend changes)

Two Alpha 30-era native backend bugs were hit while writing the module
and worked around in pure Niko:

- Native `otherwise if` + short-circuit `and`/`or` returned `nothing`
  when the first operand didn't decide — branches were split into
  nested `if`s.
- Native hoisted call arguments in `if`/`while` conditions past
  short-circuit guards — `_char_at` never panics (returns `""` past
  the end) instead of relying on a guard to prevent the call.

Both are recorded in `niko2/KNOWN_LIMITATIONS.md` for a future backend
fix. Also note: `1 is yes` evaluates to `yes` (Niko `is` is `==`), so
booleans are detected via `text()` rendering, not `value is yes`.

## Verification status

- `tests/test_stdlib2.py`: `'json'` added to `MODULES` — all 24 doc
  examples executed 3-way (VM/WASM/native) + determinism double-run,
  `STDLIB.md` regenerated and freshness-checked. Green.
- `tests/test_json.py` (new): 14 malformed inputs asserting the exact
  error character position on all three backends; `\u0041`/`\u007f`
  escapes and the above-U+007F rejection; the ≥1e15 rejection
  (`1e15`, `-2e15` error; `±999999999999999` parse everywhere);
  the `1.0` rendering divergence asserted per-backend; a round-trip
  battery (`parse(stringify(x)) is x`: nested, unicode text, empty
  containers, exponents/negatives, duplicate keys, escape-heavy
  strings, 50-deep nesting); key insertion order; `stringify` of a
  function errors on every backend; unicode round-trip asserts
  VM/native success and WASM failure (the known backend limit —
  flip it when the backend is fixed). All green.
- Full suite green: `tests/test_*.py` (27 files), `tests/run_tests.py`
  (26 Niko 1 cases), `tests/run_niko2_tests.py` (16 Niko 2 cases +
  nikoir round trip).

See `ALPHA31_DESIGN.md` (with "As built").
