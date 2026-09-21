# Alpha 12 Design — match as an expression

## Goal
`match` becomes usable as an expression: its value is the value of the
selected arm. Same semantics on VM / WASM / native (3-way differential),
typed result, checker-enforced exhaustiveness.

## Syntax

```
set label to match score:
    when n if n is at least 90:
        "honors"
    when n if n is at least 50:
        "pass"
    otherwise:
        "fail"
```

Positions where a match expression may appear (statement-level, where the
parser controls the following lines):
- `set NAME to match EXPR:` / `set NAME: TYPE to match EXPR:`
- `give back match EXPR:`
- `say match EXPR:`

Arms are indented under the `set`/`give back`/`say` line, exactly like arms
under a `match` statement. The full pattern language from Alpha 6 + Alpha 11
(literals, `ok`/`error`, bare bindings, comma alternatives, guards,
`[...]`/`{...}` nested patterns) works unchanged.

Each arm body is a statement list; the arm's **value** is the value of its
**last statement**, which must be an expression statement (bare expression,
e.g. `"A"`, `a + b`, a call). The checker rejects an arm whose body does not
end with an expression:

```
match arm must end with an expression to produce a value
```

(`say` is a statement, not an expression, so an arm ending in `say` is
rejected — the value would be meaningless. This is deliberate.)

## Typing

- The subject is checked as usual; patterns bind names as in statements.
- Arm type = type of the arm's final expression.
- All arms (including `otherwise`) must agree: if any pair is incompatible in
  both directions → `match arms produce different types: number vs text`.
- Result type = the first non-`any` arm type (or `any` if all are `any`).
  `set x: text to match ...:` enforces compatibility via the existing `set`
  annotation check.

## Exhaustiveness (checker-enforced, expressions only)

A match **expression** is exhaustive when it has an `otherwise:` arm, OR its
last `when` arm has no guard and one of its alternatives is a bare `MatchBind`
(catch-all). Otherwise the checker raises:

```
match expression must be exhaustive — add an otherwise: arm
```

with line + column of the match expression. Match **statements** keep their
existing lenient behavior (no match → execution continues).

## Evaluation order

Per arm: subject (evaluated once) → pattern test → bindings → guard → body;
first match wins; a failed guard falls through to the next pattern/arm.
Identical to match statements; the only addition is that the winning arm's
final expression value becomes the match expression's value.

## VM implementation

- New AST node `MatchExpr(line, expr, cases, otherwise)`.
- `Compiler.expr` gains a `MatchExpr` case mirroring `match_stmt`:
  evaluate subject → per-pattern `if (test) { binds; if (guard) { <body
  stmts except last>; <last expr>; STORE $matchval; goto end } }` →
  `otherwise` → end → `LOAD $matchval` (the expression's value).
  `$matchval` is pre-initialized to `nothing` as defensive insurance.
- **No new slot hazards.** A nested match in an arm body clobbers `$match`,
  but later tests only run when earlier tests failed (so their bodies never
  ran) — the same reason nested match *statements* were never broken.
  `$pat{n}` slots are re-STOREd before each use. (If a future alpha allows
  match expressions inside guards or general expression positions, revisit
  with depth-indexed `$match{d}` slots.)

## WASM / native implementation

- WASM: `_gen_match_expr` mirroring `_gen_match`; each arm's final
  expression value → a fresh result local; the local is left on the stack as
  the expression's value. Fresh locals per call keep nesting safe.
- Native: `gen_expr` gains a `MatchExpr` case that emits the match as
  statements (into a fresh temp, via the existing `_gen_match` machinery
  generalized to a result variable) and yields the temp name as the C
  expression. Emission order works because `gen_expr` runs before the
  enclosing `_emit`.

## Parser notes

- Extract arm parsing from `parse_match` into a shared
  `parse_match_arms(lines, j, arm_ind)` helper.
- In `parse_stmt`: `set` branch — if the captured value starts with `match `
  and the line ends with `:`, parse a match expression + arms.
  Same interception for `give back` and `say`.
- `MatchExpr` participates in formatter (`format_pattern` arms, multi-line
  `set x to match v:`), symbols walk, and closures read/write analysis
  (mirror the `MatchStmt` branches).

## Tests

- `tests/niko2_cases/match_expr.niko` (+`.out`): values from literal arms,
  guards, list/record patterns, nested matches, match-in-guard,
  `set`-annotation, `give back`, `say` positions.
- `tests/test_match_expr.py`: checker rejections (non-exhaustive, arm ending
  in `say`/non-expression, mixed arm types) + legal programs; 3-way
  VM/WASM/native differential byte-identical.
- Full suite stays green; Niko 1's 26 cases untouched.

## Docs

RELEASE_NOTES_ALPHA12.md; NIKO_AI_HANDOFF.md (Alpha 12 item), NEXT_STEPS.md,
niko2/KNOWN_LIMITATIONS.md, NIKO_AI_BRIEF.md §3b updated.
