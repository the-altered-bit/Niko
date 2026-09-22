# Release notes — Alpha 35: `niko2 migrate`, the Niko 1 → Niko 2 migration advisor

Niko 2 is deliberately not Niko 1: a real grammar, a typechecker, and no
Python fallthrough. That leaves everyone with a Niko 1 program asking the
same question — *what do I have to change?* Alpha 35 answers it with
`niko2 migrate`, a two-phase analyzer that reports every divergence with
a line number, a severity, and a pointer into the new `MIGRATION_GUIDE.md`
difference catalog — and applies the mechanical fixes with `--fix`:

```bash
niko2 migrate program.niko              # report: errors, warnings, notes
niko2 migrate --fix program.niko        # apply the safe rewrites in place
niko2 migrate --fix --stdout prog.niko  # fixed source to stdout, report to stderr
```

`niko2 migrate` exits 1 when errors remain, 0 when only warnings/notes (or
nothing) remain, 2 on usage/IO errors.

## What changed

- **`niko2/migrate.py`** (new, ~1200 lines): the analysis engine.
  **Phase A** is a heuristic line scan with Niko-1-faithful lexing
  (strings masked, `#` comments stripped per Niko 1's own rules).
  **Phase B** runs Niko 2's own parser + typechecker and maps the first
  diagnostic to a named finding. The two phases cross-validate: if Niko 2
  accepts the file outright, heuristic *errors* are dropped as false
  positives (a clean check is ground truth) — except the two divergences
  the checker can't see. Findings are `(line, severity, code, message,
  optional fix)`; `apply_fixes` runs a bounded fixpoint with an
  idempotency guard, so `--fix` never needs a second pass.
- **`MIGRATION_GUIDE.md`** (new): the difference catalog — §2 hard
  errors (boolean conditions, `not` typing, builtin-not-a-value, operand
  types), §3 the 21 Niko 1 syntaxes Niko 2 rejects (`repeat forever`,
  `use <python-lib>`, `ask(...)`, `remove(l, x)`, `random()`, `range()`,
  `in`, `=`, `import os`, f-strings, hex, `//`, slices, `say "a" "b"`,
  `add [..] to l`, default args, `set d.k`, `join(l)`, bare `for`, `+=`),
  §4 the 10 run-but-differently behaviors (text rendering, `join`
  rendering, `now()`, banker's `round`, `split(t, "")`, `item 0`,
  1-based list indexing, chained comparisons, `not` precedence, function
  globals), §5 notes, §6 what migrates cleanly, §7 Niko 2 features worth
  adopting, §8 the advisor-vs-transpiler contract. Every finding cites
  its section.
- **CLI** (`niko2/cli.py`): `migrate` added to the command list with the
  same argv-hoist pattern as `fmt`'s multi-arg handling. `--fix` prints
  `applied automatic fixes: <codes>` and re-analyzes so fixed findings
  disappear from the report.
- **`niko2/KNOWN_LIMITATIONS.md`**: three audit findings recorded —
  `use <python-lib>` is *silently ignored* (the most dangerous item in
  the guide: nothing fails at the `use` line), functions can't write
  top-level variables (Niko 1 printed `7`, Niko 2 prints `0` for
  `tests/cases/globals.niko`), and one unverified truthiness question
  marked as needing a probe.
- **Tests**: `tests/migrate_cases/` (12 Niko 1 fixtures + pinned
  `.expected.json` + 4 `.fixed.niko` round-trip expectations) and
  `tests/test_migrate.py` (pinned findings incl. line numbers, fix
  round-trip + idempotency + clean re-analysis, CLI exit codes and
  `--fix` behavior, zero *errors* over `tests/niko2_cases/` +
  `niko2/stdlib/`, spot checks on the Niko 1 suite).

## What the audit found (verified differentially, ~70 probes)

Two divergences nobody had documented: `use math` parses and typechecks
on Niko 2 but imports nothing, and function writes to top-level
variables are silently function-local on Niko 2 (in-place mutation via
`put`/`remove`/`set item` still reaches the shared object in both).
Both are in the guide (§3.2, §4.10) and detected (`use-python`,
`global-write`). Four bugs were found and fixed while building the tool:
`_checker_finding` dropped the filename so relative imports never
resolved; `not-is-in`'s fix wasn't idempotent (parens quadrupled);
`split-empty` searched for `""` on the string-masked line and could never
fire; chained-comparison detection false-fired on `list<number>`
generics (fixed by stripping generic shapes iteratively).

## Deliberate limits

`migrate` is an **advisor, not a transpiler**: `--fix` only applies
semantics-preserving rewrites (repeat-forever, ask/remove/random/range
call forms, bare `in`, `=`, hex, `//`, `say "a" "b"`, `set d.k`,
`join(l)`, bare `for`, `+=`, `not x is in y`). Everything semantic —
`if name:` (non-empty? is-yes?), `use mylib`, chained comparisons,
global writes — is reported for a human to decide. The intended workflow
is iterative: run, fix, re-run until the errors are gone, eyeball the
warnings.
