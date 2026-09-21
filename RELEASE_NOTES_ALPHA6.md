# Niko 2.0 Alpha 6 — release notes

Alpha 6 completes the language core the roadmap set out: **real generic
types**, an **option/result error model**, and **pattern matching**. The
package dependency graph (`niko2 deps`) and lock file (`niko2 lock`) from
the roadmap already shipped earlier. Everything below is implemented,
typechecked, compiled to bytecode, and covered by tests.

## WIP1 — `list<T>` generics are checked for real

- **List literals infer element types.** `[1, 2, 3]` is `list<number>`,
  `[["a"]]` is `list<list<text>>`; heterogeneous literals (`[1, "a"]`)
  stay legal Niko and infer `list<any>`; `[]` stays plain `list`.
- **Annotations are enforced.** A list literal assigned to a `list<T>`
  variable is checked element by element:
  `set xs: list<number> to ["a"]` →
  `cannot assign text to list<number> variable "xs"`.
- **`put` checks the value** against the list's element type
  (`cannot put text in list<number>`); **`X[i]` and `item_of(i, X)` yield
  the element type** (so `set s: text to nums[0]` fails when `nums` is
  `list<number>`); **`for each x in XS` types `x`** as the element type.
- **Nested annotations parse:** the `set` regex now allows nested angle
  brackets, so `set grid: list<list<number>> to [[1, 2], [3]]` works
  (previously a parse error).
- Tests: `tests/niko2_cases/generics.niko` (+`.out`) extended with
  element-type, `put`, indexing, nested, and heterogeneous cases;
  `tests/test_generics.py` asserts six rejection cases (message + exit 1)
  and three still-legal cases.
- Still future: generic-aware builtin *return* types (`sorted`, `unique`,
  … still return `any`), call-argument checking against parameter
  annotations, `map<K,V>` value-type inference.

## WIP2 — option/result error model

Failure is now a value, not an exception. Two types:

- `option<T>` — a `T` or `nothing`. A plain `T` is a valid `option<T>`,
  so `set x: option<number> to 5` and `set x: option<number> to nothing`
  both check; `set x: option<number> to "a"` is rejected.
- `result<T>` — `ok(value)` or `error(message)`. Niko errors are already
  plain-English text, so the error side is always text.

New builtins (`use "result"` group in the stdlib listing):

| helper | meaning |
|---|---|
| `ok(v)` / `error(msg)` | build a result |
| `is_ok(r)` / `is_error(r)` | test it |
| `unwrap(r)` | the value, or fail with the error message itself |
| `unwrap_or(r, default)` | the value, or `default` (also unwraps options: `unwrap_or(nothing, 0)` → `0`) |
| `error_message(r)` | the message text of an error result |
| `try_read_file(name)` | `ok(text)` or `error("I couldn't find the file …")` |
| `try_number(text)` | `ok(number)` or `error("I can't turn … into a number.")` |

The typechecker infers `ok(42)` as `result<number>`, types `unwrap` /
`unwrap_or` as `T` for a `result<T>`, and rejects assigning a bare value
to a `result<T>` variable (`cannot assign number to result<number>`).
`say ok(42)` prints `ok(42)`; `say error("boom")` prints `error("boom")`.

The raising builtins (`read_file`, `number`, …) are unchanged — the
`try_*` variants are the opt-in value-based path.

## WIP3 — pattern matching

The `match` statement (the old `match is reserved for Niko 2.1` stub is
gone — it shipped early):

```
match try_number(ask "age?"):
    when ok n:
        say "you are " + text(n)
    when error msg:
        say "that wasn't a number: " + msg

match day:
    when "sat", "sun":
        say "weekend"
    when nothing:
        say "no day set"
    when d:
        say "weekday: " + d
    otherwise:
        say "unreachable, but allowed"
```

- Patterns: literals (`1`, `"quit"`, `yes`, `nothing`), `ok name` /
  `error name` destructuring (name binds the value / the message text),
  bare-name bindings (`when d:`), comma-separated alternatives
  (`when "sat", "sun":`). First match wins.
- `otherwise:` is optional; if nothing matches, execution simply
  continues after the `match`.
- The checker types bindings (`ok n` on a `result<number>` gives `n`
  the type `number`) and rejects `ok`/`error` patterns against a
  statically known non-result (`cannot match "ok" against number`).
- The compiler desugars `match` to the existing jump opcodes plus the
  `is_ok` / `unwrap` builtins — the subject is evaluated once, no new VM
  opcodes were needed.
- `niko2 format` round-trips `match` blocks.

Tests: `tests/niko2_cases/results.niko` (+`.out`),
`tests/niko2_cases/match.niko` (+`.out`),
`tests/test_results_match.py` (five checker rejections, six still-legal
cases, `unwrap` runtime messages, hermetic `try_read_file` round trip),
match cases added to `tests/test_formatter.py`.

## Still future (Alpha 7+)

- Match guards (`when n if n > 0:`), list/record patterns, `match` as an
  expression.
- `map<K,V>` generics, generic builtin return types.
- Alpha 7: language server, VS Code extension, debugger protocol.
- Alpha 8: WASM backend. Alpha 9: native backend.

## Validation

- 12 Niko 2 cases green, 26 Niko 1 cases green (engines behavior-identical).
- `test_diagnostics`, `test_formatter`, `test_generics`, `test_stdlib`,
  `test_results_match`, `test_project_deps` all pass.
