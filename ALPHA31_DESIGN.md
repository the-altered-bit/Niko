# Alpha 31 design: `json` stdlib module (parse/stringify)

## Problem

Niko 2's stdlib (Alpha 14: `text`, `math`, `lists`, `records`) has no
JSON support. Any program talking to a web API, a config file, or the
playground's JS host ends up hand-rolling string surgery. JSON is
small enough to write in pure Niko with zero backend changes — a
recursive-descent parser plus a serializer — and it exercises the
stdlib contract hard: deep recursion, string indexing, error
propagation via `unwrap(error(...))`, and byte-identical output on
all three backends.

## Design

One new file, `niko2/stdlib/json.niko`, following the Alpha 14 stdlib
conventions: every `to` (public and `_`-private) carries a doc comment
in the `tests/test_stdlib2.py` format (`sig` line, prose,
`# example: code -> expected` lines); no top-level `say`/`set`; passes
`niko2 check`; `STDLIB.md` regenerated from the doc comments.

**Public API** (2 functions):

- `parse(text) -> any`: one JSON document → Niko values. Objects →
  records, arrays → lists, strings → text, numbers → numbers,
  `true`/`false`/`null` → `yes`/`no`/`nothing`. Leading/trailing
  whitespace allowed; anything else is a parse error naming the
  1-based character position.
- `stringify(value) -> text`: compact JSON, record keys in insertion
  order (the order `parse` produces), text quoted+escaped, numbers via
  `text()`, `yes`/`no`/`nothing` → `true`/`false`/`null`. Anything
  else (functions, and non-finite numbers if they could arise) →
  `json stringify error`.

**Internals** (22 `_`-prefixed helpers): the parser threads a
`{pos}` cursor record through `_parse_value` / `_parse_object` /
`_parse_array` / `_parse_string` / `_parse_escape` / `_parse_unicode` /
`_parse_number` (+ small predicates `_is_ws`, `_is_digit`,
`_char_at`, `_expect_word`, `_parse_literal`, `_err_text`,
`_hex_val`, `_u_table`, `_decimal_exp`). The serializer mirrors with
`_stringify_string` / `_stringify_list` / `_stringify_record` /
`_stringify_number` / `_escape_char`.

**Error model**: parse errors are raised with
`unwrap(error(_err_text(pos, msg)))`, producing
`json parse error at character N: <msg>`. `unwrap` on an error
re-raises the message itself, so the error is native on every backend
(VM/native/WASM all surface guest panics) — no backend-specific error
plumbing needed.

**Deliberate semantic choices**:

- Duplicate object keys: last value wins (JSON-common behavior).
- `\uXXXX` escapes: decoded for code points ≤ U+007F via a 128-char
  `_u_table`; above that is a position-named parse error (see
  limitations).
- Numbers with magnitude ≥ 1e15: rejected with `number out of range`
  (see limitations).
- Booleans detected via `text()` rendering (`"True"`/`"yes"`,
  `"False"`/`"no"`) rather than `value is yes` — because Niko `is` is
  equality and `1 is yes` evaluates to `yes`.
- `_char_at` never panics: past the end it returns `""` (see the
  native arg-hoisting workaround below).

**Tests**: `'json'` in `MODULES` in `tests/test_stdlib2.py` (doc
examples become the 3-way + determinism corpus; `STDLIB.md` freshness
enforced) + a new `tests/test_json.py` for what doc examples can't
cover: malformed-input error positions, `\u` escapes, the ≥1e15
rejection, the `1.0` rendering divergence (asserted per-backend),
round-trip battery, 50-deep nesting, key insertion order, stringify
error cases.

## As built

(appended by the final worker; everything below is verified against
the tree and the test suite)

### What the module is

`niko2/stdlib/json.niko` (21,680 bytes at commit): `parse` +
`stringify` + 22 `_`-prefixed helpers, all doc-commented; 24 doc
examples, every one executed 3-way (VM/WASM/native) byte-identical
plus a determinism double-run by `tests/test_stdlib2.py`. `niko2
check` clean. `STDLIB.md` regenerated via `--write-docs` and
freshness-checked by the suite.

### The three limitations, as tested

1. **`\uXXXX` above U+007F rejected.** `_parse_unicode` decodes four
   hex digits and rejects code > 127 with
   `\u escapes above U+007F are not supported` at the escape's
   position. `\u0041` → `A`, `\u007f` → length 1, verified 3-way.
   On VM/native, raw UTF-8 in JSON strings parses fine; on WASM it
   does not (next paragraph). `stringify` passes multibyte text
   through untouched on all three backends.
2. **Magnitude ≥ 1e15 rejected.** `_parse_number`/`_decimal_exp`
   compute the value and reject with `number out of range` at
   character 1 before any backend-specific float conversion can
   disagree. `1e15` and `-2e15` error on all three backends;
   `999999999999999` and `-999999999999999` parse and print
   identically everywhere (exactly representable in f64).
3. **`1.0` renders `1.0` (VM) vs `1` (WASM/native).**
   `tests/test_json.py` asserts per-backend expected values rather
   than one value: `json.stringify(1.0)` → `1.0` on VM, `1` on
   WASM/native; `json.stringify(2.5)` → `2.5` everywhere. Both are
   valid JSON for the same value; no doc example hits the divergent
   case, so the 3-way doc corpus stays byte-identical.

### The two Alpha 30-era native bugs, worked around in pure Niko

No backend code was touched. Both workarounds live in `json.niko`
and are pinned by `tests/test_json.py` behavior (the module simply
would not work on native without them):

- **(a) Native `otherwise if` + short-circuit `and`/`or` returns
  `nothing` when the first operand doesn't decide.** Where the
  parser needed "first check A, else check B" logic combined with
  short-circuit operators, the branches were split into nested
  `if`s instead of `otherwise if` chains.
- **(b) Native hoists call arguments in `if`/`while` conditions past
  short-circuit guards.** Code shaped as
  `if pos is at most length(s) and _char_at(s, pos) is ...` is
  unsafe on native: the call can evaluate before the guard. So
  `_char_at` is total — it returns `""` past the end instead of
  panicking — and call sites never rely on a guard to prevent the
  call.

Both are recorded in `niko2/KNOWN_LIMITATIONS.md` under the new
"Alpha 31" section for a future backend fix.

### New backend divergence found by the tests (documented, not fixed)

`tests/test_json.py`'s unicode round-trip
(`json.parse(json.stringify("héllo wörld"))`) passes on VM and
native but **fails on WASM** with
`json parse error at character 14: unterminated string`. Bisected to
a WASM backend bug, not a module bug: `while yes:` (unbounded) loops
miscompile string indexing (`item_of`) on strings containing
multibyte UTF-8 — a minimal `while yes:` + `item_of` repro panics
the host with `string index out of range`, while the same loop with
a bounded condition, or the same indexing outside `while yes:`,
works. ASCII strings are unaffected on all backends, and WASM
`stringify` of multibyte text is correct — only the parse path
(which scans with `while yes:`) breaks. Per the project rule for
new backend divergences, this is documented as a limitation
(`niko2/KNOWN_LIMITATIONS.md`, the module header, and
`tests/test_json.py` asserting the WASM failure explicitly with a
"flip when fixed" comment) rather than fixed in the backend.

### Verification numbers

- `tests/test_stdlib2.py`: 5 modules green, 3-way identical,
  deterministic, `STDLIB.md` fresh.
- `tests/test_json.py`: 14 malformed-input position cases × up to 3
  backends, `\u` escapes, ≥1e15 rejection, `1.0` per-backend
  rendering, 8 round-trip expressions × 3 backends (+ unicode
  VM/native), 50-deep nesting, key order, stringify-of-function
  error × 3 backends, determinism double-run — all green.
- Full suite: `tests/test_*.py` (27 files), `tests/run_tests.py`
  (26 Niko 1 cases), `tests/run_niko2_tests.py` (16 Niko 2 cases +
  nikoir round trip) — green.
