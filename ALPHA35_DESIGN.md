# Alpha 35 design — `niko2 migrate`

## Goal

Give Niko 1 users a mechanical path to Niko 2: an analyzer that finds
every divergence with a line number and a guide section, applies the
safe rewrites itself, and stays silent on code that already migrates.

## Why two phases

A pure heuristic scan can't know what Niko 2 accepts (false positives);
Niko 2's checker alone can't name *Niko 1* idioms (it just says
"unexpected ':'" for `repeat forever:`). So:

- **Phase A (heuristics)** knows the 40 Niko 1 idioms from the
  line-by-line `niko.py` audit — boolean conditions, `not` typing,
  builtin-as-value, call forms (`ask`/`remove`/`random`/`range`),
  Python fallthrough (`=`, f-strings, hex, `//`, slices, `import os`,
  `+=`), Niko-1-only syntax (`repeat forever`, bare `in`/`not in`,
  `for`, `add [..] to`), global writes, and the 10 behavioral warnings.
  Each rule returns `(message, fix-or-None)`; every fix is a
  semantics-preserving rewrite (verified by re-running the Niko 1
  program where it mattered).
- **Phase B (the checker)** runs Niko 2's own `parse` + `check` via
  `compile_source(src, name)` and maps the *first* diagnostic to a
  finding (`unknown-name`, `cond-boolean`, `not-boolean`, `builtin-value`,
  `arith-type`, `n2-diagnostic` for the rest).

## Cross-validation rules

1. **Clean check wins.** If Phase B passes the file with no diagnostic,
   heuristic *errors* are false positives and are dropped. This killed
   the `if is_prime(n):` false positive on `tests/cases/primes.niko`
   (the checker proves the call returns boolean). Warnings/notes always
   stand — they describe behavior, not rejection.
2. **The checker is blind to two things**, kept unconditionally:
   `use-python` (`use math` typechecks but silently ignores) and
   `join-arity` (`join([1,2])` typechecks but fails at run time).
3. **Dedup:** a heuristic finding on `(line, code)` beats the checker's;
   generic checker codes (`n2-diagnostic`, `unknown-name`) are dropped
   when the heuristic already has an *error* on that line; specific
   checker diagnoses the heuristic missed are kept (e.g. `not-boolean`
   alongside `not-is-in`).

## Lexing discipline

All Phase A rules run on the *masked* line (strings replaced by spaces,
`#` comments stripped per Niko 1's own rules) so `"say \"hi\""` never
triggers say-rules and `# use math` in a comment is ignored. Span
information (string literal positions) is kept for rules that need it
(`say-adjacent`, `split-empty`, masking-aware `in`). Two real false
positives were found and fixed this way: `put x in list` is valid Niko 2
(and migrates cleanly), and `list<number>` generics faked chained
comparisons via `<`/`>` (fixed by iteratively stripping generic shapes).

## Fixpoint + idempotency

`apply_fixes` runs the fix list to fixpoint (bounded at 10 passes;
`say "a" "b" "c"` needs two). Every fix recomputes line spans from the
current text of the line it was reported on, and each fix is guarded to
be a no-op when the line is already in the fixed form (the `not-is-in`
fix quadrupled parens before the guard). `--fix` re-analyzes after
applying so fixed findings disappear from the report.

## What's deliberately NOT in this alpha

- No auto-fix for anything semantic: `if name:`, `use mylib`,
  `add [..] to l`, chained comparisons, global writes, slices. The tool
  reports; the human decides. (This is §8 of the guide.)
- No WASM/native differential in the advisor — the checker's verdict
  is backend-agnostic by design.
- No whole-program migration of multi-file projects (each file is
  analyzed independently; imports resolve for the checker's benefit
  only).

## Files

- `niko2/migrate.py` — everything (rules table, fixes, two-phase
  analysis, report formatting)
- `niko2/cli.py` — `cmd_migrate`, `--fix`/`--stdout`, argv hoist
- `MIGRATION_GUIDE.md` — the user-facing catalog
- `tests/migrate_cases/` + `tests/test_migrate.py` — fixtures and tests
