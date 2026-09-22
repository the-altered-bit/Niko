# Release notes — Alpha 33: WASM multibyte string indexing fix

The WASM backend miscompiled `item_of` on multibyte text: indexing the
last character(s) of a string like `"héllo"` returned `""` instead of
the character. In a `while yes:` scan-to-terminator loop — exactly the
shape `json.niko`'s string parser uses — the terminator never matched,
the loop ran past the end, and the program died with `string index out
of range`. One codegen fix in `niko2/backends/wasm.py`; VM and native
were already correct and are untouched.

## What changed

- **`niko2/backends/wasm.py` (`_b_item_of`)**: the text branch kept the
  string's byte length in a local, then overwrote it with the *character*
  count from `utf8_len`, and passed that count as the *byte* length
  argument to `utf8_byte_offset`. For ASCII the two are equal so nothing
  showed; for multibyte text the byte scan stopped early and the end
  offset of the final character(s) came back truncated. The byte length
  now lives in its own local (`blen`) and is passed to both
  `utf8_byte_offset` calls. All other `utf8_byte_offset` call sites
  already kept the two separate — `item_of` was the only one with the
  mix-up.
- **No host-contract change**: `wasm_host.cjs`, the playground host,
  and `WASM_DESIGN.md` are untouched — the helper was always correct,
  only its caller passed the wrong length.

## Tests

- New `tests/test_wasm_unicode.py`: 20 cases, 3-way differential
  (VM/WASM/native, byte-identical output) — `item_of` at every position
  of `"héllo"`, `"a😀b"` and `"αβγ"` including negative indices,
  `length` of multibyte strings, `while`-loop and `while yes:`
  terminator walks, `reversed`/`split`/`join`/concatenation on multibyte
  text, plus an out-of-range case asserting all three backends fail
  loudly with `string index out of range`.
- `tests/test_json.py`: the WASM unicode round-trip assertion (marked
  "flip when fixed" in Alpha 31) is flipped — `json.parse` of raw
  multibyte JSON now succeeds on WASM like VM/native.
- `niko2/stdlib/json.niko` header and `STDLIB.md` regenerated: the
  "raw multibyte UTF-8 fails on WASM" limitation is gone. (`\uXXXX`
  escapes above U+007F remain a parse error on every backend — unchanged,
  still documented.)

## Known limitations

None new. `niko2/KNOWN_LIMITATIONS.md` moves the Alpha 31 entry from
known-bug to fixed-in-Alpha-33, with the root cause written up.
