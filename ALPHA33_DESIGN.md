# Alpha 33 — WASM multibyte string indexing: design

Bugfix sprint for the WASM backend bug found by Alpha 31's `json`
testing (documented in `niko2/KNOWN_LIMITATIONS.md` with a minimal
repro, asserted in `tests/test_json.py` with a "flip when fixed"
comment). No language change, no new builtins, no host-contract change.

## The bug (as reported)

```niko
to f2 with s: text, cur: map -> text:
    set out to ""
    while yes:
        set c to item_of(cur["pos"], s)
        if c is "\"":
            set cur["pos"] to cur["pos"] + 1
            give back out
        otherwise:
            set out to out + c
            set cur["pos"] to cur["pos"] + 1
say f2("\"héllo\"", {pos: 2})
```

VM/native printed `héllo`; WASM died with `Line 4: string index out of
range` from the host. The report noted the same `item_of` calls outside
`while yes:` "worked" and ASCII was unaffected everywhere.

## Root cause (as built)

Bisecting with correct 1-indexed programs (`item_of` is 1-based:
`x[int(i)-1]`) showed the trigger was narrower than reported:
`item_of(5, "héllo")` — the *last* character of a multibyte string —
returned `""` on WASM while VM/native returned `"o"`. Non-final
positions were fine, which is why ad-hoc probes outside the loop
"worked": the terminator in a scan-to-the-end loop is always the last
character, so `while yes:` was the shape that reliably hit it.

In `niko2/backends/wasm.py`, `_b_item_of`'s text branch did:

```
local n = text.byte_len
local n = utf8_len(ptr, n)      # n is now the CHAR count
...
bo = utf8_byte_offset(ptr, n, j)      # n passed as BYTE len -- wrong
bl = utf8_byte_offset(ptr, n, j + 1)  # n passed as BYTE len -- wrong
```

`utf8_byte_offset(ptr, len, charidx)` scans bytes and stops at `len`
*bytes* as a safety bound. Passed the char count instead, the scan of
`"héllo"` (5 chars, 6 bytes) stopped at byte 5, so the end offset of
the final character came back as 5 instead of 6: `bl = 5 - 5 = 0`, a
zero-length slice. For pure ASCII, char count == byte length, so the
mix-up was invisible.

The empty string then failed the `c is "\""` terminator check, the loop
advanced past the string end, and the bounds check panicked with
`string index out of range` — the reported symptom.

## The fix (as built)

Three lines in `_b_item_of` (`niko2/backends/wasm.py`): the byte length
keeps its own local `blen`; `n` still becomes the char count for the
bounds check; both `utf8_byte_offset` calls take `blen`:

```
local blen = text.byte_len
local n = utf8_len(ptr, blen)
...
bo = utf8_byte_offset(ptr, blen, j)
bl = utf8_byte_offset(ptr, blen, j + 1)
```

VM (`x[int(i)-1]`) and native were already correct and are untouched.
The host helper was always correct — only its caller passed the wrong
length — so `wasm_host.cjs`, the playground's `playground.js` host, and
`WASM_DESIGN.md` need no changes.

Audited the other six `utf8_byte_offset` call sites (1767, 2427, 2693,
2742, 3075, 3166): all keep byte length and char count in separate
locals and pass the byte length. `item_of` was the only site with the
mix-up.

## Tests (as built)

- `tests/test_wasm_unicode.py` (new, 20 cases, 3-way VM/WASM/native
  differential, byte-identical output): `item_of` at every position of
  `"héllo"` / `"a😀b"` / `"αβγ"` (incl. negative indices — note the
  existing 1-based-shifted rule `x[int(i)-1]`, so `item_of(-1, s)` is the
  second-to-last char on all three backends), `length` of multibyte
  strings, bounded-`while` and `while yes:` terminator walks, bulk ops
  (`reversed`, `split`/`join`, concatenation) on multibyte text, and an
  out-of-range case asserting every backend fails with `string index out
  of range`. WASM/native legs skip cleanly when node/cc are absent.
- `tests/test_json.py`: the WASM unicode round-trip assertion flipped
  from "asserts failure" to `(0, 'yes\n')`; the KNOWN LIMIT header
  comment updated.
- `niko2/stdlib/json.niko` header + `_parse_unicode` docstring updated;
  `STDLIB.md` regenerated via `tests/test_stdlib2.py --write-docs`.
- Full suite: Niko 1 cases, Niko 2 cases + nikoir round trip, all
  `tests/test_*.py` green.

## Docs (as built)

- `RELEASE_NOTES_ALPHA33.md` (this sprint's notes).
- `niko2/KNOWN_LIMITATIONS.md`: Alpha 31 entry rewritten as
  fixed-in-Alpha-33 with the root cause; the stale "bounded while
  works" observation corrected (it only *appeared* to work).
- `NIKO_AI_HANDOFF.md`: new item 27.
- `NEXT_STEPS.md`: Alpha 33 marked complete.
- `~/MEMORY.md`: Alpha 33 line.

## Deliberately not done

- No Unicode normalization, case-mapping, or new string builtins — the
  sprint fixes indexing/length to match VM/native, nothing more.
- The `\uXXXX`-above-U+007F parse error stays: still a documented,
  intentional module limitation (uniform rejection for
  byte-identicalness), now with a corrected rationale in the docstring.
- The two native-backend workarounds in `json.niko` (`otherwise if` +
  short-circuit, call-arg hoisting) are untouched — separate bugs,
  separate sprints.
