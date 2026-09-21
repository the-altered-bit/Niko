# Niko 2.0 — Alpha 12 release notes

**Match as an expression.** `match` now produces a value: the value of the
selected arm. Same semantics on all three backends, byte-identical.

## New syntax

```
set grade to match score:
    when n if n is at least 90:
        "honors"
    when n if n is at least 50:
        "pass"
    otherwise:
        "fail"
say grade
```

A match expression can appear as the whole right-hand side of `set`
(annotations work: `set x: text to match ...:`), after `give back`, and
after `say`:

```
to label with v:
    give back match v:
        when [a]:
            "single " + text(a)
        when x:
            "other"
```

The full pattern language (Alpha 6 + Alpha 11 — literals, `ok`/`error`,
bare bindings, comma alternatives, guards, `[...]`/`{...}` nesting) works
unchanged in expression position.

## Arm values

Each arm body is a statement list; the arm's **value** is the value of its
**last statement**, which must be an expression:

```
set v to match [1, 2, 3]:
    when [first, ...rest]:
        say "saw rest"      # statements are fine...
        text(first) + "/" + text(length(rest))   # ...but the arm must end with an expression
    otherwise:
        "empty"
```

An arm ending in `say` (a statement, not an expression) is a checker
error: `match arm must end with an expression to produce a value`.

## Typing and exhaustiveness

- Every arm's final expression is typed; all arms must agree, or the
  checker reports `match arms produce different types: number vs text`.
  The match expression's type is that common type.
- A match **expression** must be exhaustive — it needs an `otherwise:` arm,
  or its last `when` must be an unguarded catch-all (`when x:`). Otherwise
  the checker reports `match expression must be exhaustive — add an
  otherwise: arm` with line/column. Match **statements** keep their lenient
  behavior (no match → execution simply continues).

## Semantics notes

- Evaluation order is identical to match statements: subject (once) →
  pattern test → bindings → guard → body; first match wins; a failed guard
  falls through. The winning arm's final expression value becomes the
  match's value.
- Match expressions nest freely in arm bodies (each has its own subject
  and result slots, so nesting is safe).

## Backends

All three backends (VM, WASM, native) implement the same semantics;
`tests/test_match_expr.py` checks 6 programs byte-identical across VM,
WASM, and native, and `tests/niko2_cases/match_expr.niko` covers values
from guards, list/record patterns, nested matches, and all three
expression positions on the VM.

## Limits

- A match expression must be the whole right-hand side (`set x to match
  ...:`) — it can't appear inside a larger expression, a guard, or as a
  call argument yet.
