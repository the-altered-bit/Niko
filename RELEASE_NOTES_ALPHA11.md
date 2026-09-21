# Niko 2.0 — Alpha 11 release notes

**Match guards + list/record patterns.** The `match` statement (Alpha 6)
grows up: arms can now carry a boolean guard, and patterns can take apart
lists and records — on all three backends, byte-identical.

## New syntax

```
match order:
    when [item, ...rest] if item is "cake":
        say "cake plus " + text(length(rest)) + " more"
    when [a, b]:
        say text(a + b)
    when {name: n, age: a} if a is at least 18:
        say n + " is an adult"
    when {name: n}:
        say n + " is a minor"
    otherwise:
        say "no match"
```

**Guards** — `when <patterns> if <expr>:`: the arm runs only if the pattern
matches *and* the guard is true. The guard is checked after the bindings,
so it can use them (`when [a] if a is bigger than 10:`). A failed guard
falls through to the next pattern/arm, exactly like a failed match. Guards
must be boolean, like `if`/`while` conditions.

**List patterns** — `[a, b]` matches a list of exactly two items; `[]`
matches only the empty list; `[first, ...rest]` binds the head and the
remaining items (possibly empty); `[...all]` binds the whole list.
`...rest` must come last, and there can be only one. A list pattern
against a non-list simply doesn't match.

**Record patterns** — `{name: n, age: a}` matches a record having (at
least) those keys and binds each through its sub-pattern. Extra keys are
fine; a missing key (or a non-record subject) means no match.

Patterns nest freely: `when [[a], {x: b}]:`, `when [ok v]:`,
`when {scores: [top, ...rest]}:`.

## Semantics notes

- Evaluation order per arm: subject (once) → pattern test → bindings →
  guard → body. First match wins.
- Capture is by position for lists (Niko's 1-based indexing doesn't apply
  inside patterns — `[a, b]` is just "two items").
- The typechecker types list element bindings from `list<T>` (`list<number>`
  gives `number` elements; `...rest` gives `list<number>`), and statically
  rejects a list pattern against a known non-list (and record patterns
  against known non-records) with a clear error. Record fields type as `any`.
- `ok`/`error` patterns still take a bare name (`when ok [a]:` is a parse
  error — use `when ok v:` then match on `v`).
- Malformed patterns get line/column errors: `...rest` not last, two rests,
  bad rest names, record fields without `key: pattern`, `if` with no
  condition.

## Backends

All three backends (VM, WASM, native) implement the same semantics;
`tests/test_match_patterns.py` runs a 3-way differential (byte-identical
stdout, skipping cleanly without node/cc). Implementation notes:

- VM: three new tiny opcodes (`IS_LIST`, `IS_RECORD`, `LIST_SLICE`); nested
  subject values live in hidden `$patN` slots (not valid identifiers, so
  they can't be captured); pattern bindings are plain `STORE`s, so closures
  work over them unchanged.
- WASM: tag + length/item host helpers; guard as a nested `if_`.
- Native: `_gen_match` restructured from an `if/else if` chain to
  per-pattern `if (test) { binds; if (guard) { body; goto end; } }`;
  new `nval_list_slice` / `nval_record_has` runtime helpers.

## Tests & docs

- `tests/niko2_cases/match_patterns.niko` (+`.out`): 20-line VM-suite
  coverage (guards, fallthrough, nesting, rest edges, records).
- `tests/test_match_patterns.py`: 15 checker rejections + 8 legal programs
  (with message fragments asserted) and the 3-way differential.
- Formatter, LSP symbols (hover/go-to-definition on the new bindings), and
  closure analysis all understand the new nodes.
- Full suite green: 26 niko1 cases, 14 niko2 cases + nikoir round trip,
  every `tests/test_*.py`.

## Still future work

`match` as an expression (statement-only for now).
