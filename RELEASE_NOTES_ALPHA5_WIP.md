# Niko 2.0 Alpha 5 (work in progress)

This is not a full alpha yet — it covers the Alpha 5 roadmap items as they
land, plus the bug-fixing and test infrastructure that came up while
verifying them. See `niko2/KNOWN_LIMITATIONS.md` for the fuller picture of
what Niko 2 can and can't do right now.

## WIP12 — Niko 1 stdlib gap closed (this snapshot)

- The 9 missing standard-library helpers are now implemented in Niko 2:
  `starts_with`, `ends_with`, `count_of`, `pick`, `write_file`,
  `append_file`, `read_file`, `read_lines`, `file_exists` — with Niko 1's
  semantics (e.g. `I couldn't find the file "x".`, `I can't pick from an
  empty list.`, 1-based positions, `write_file`/`append_file` format values
  with the same `fmt` as `say`).
- Wired into both builtin registries (`niko2/vm.py` for the VM,
  `niko2/runtime.py` for the tree-walker and `stdlib.module_symbols`),
  registered as known names in the typechecker (so `check`/`run` accept
  them and typo suggestions can propose them), and grouped in
  `niko2/stdlib.py` (`text` += `starts_with`/`ends_with`, `collections` +=
  `count_of`/`pick`, new `files` group).
- **Files decision:** real files, relative paths resolved against the
  process working directory — matching Niko 1's desktop engine. Documented
  in `niko2/KNOWN_LIMITATIONS.md`; `stdlib.module_symbols('files')` is the
  seam for a future sandboxed/virtual-files backend.
- Tests: `tests/niko2_cases/stdlib_text.niko` (+ `.out`) for the
  deterministic helpers; `tests/test_stdlib.py` drives `niko2 run` in a
  TemporaryDirectory for the file helpers (write/append/read round trip,
  missing-file error, exit codes) and `pick` (membership + empty-list
  error).

## WIP11 — source columns and carets in diagnostics

The remaining Alpha 5 diagnostics item is done: errors now point at the
exact spot, not just the line.

- **New module `niko2/diagnostics.py`**: a `Diagnostic` exception carrying
  `line` / `col` / `end_col`, plus a `token` hint for passes that know the
  offending text but not its position (the typechecker works on the AST
  without the source — the formatter resolves the token against the source
  line). `format_diagnostic()` renders:
  ```
  Niko error in prog.niko: line 1, column 5
    say totl(42)
        ^^^^
  unknown name "totl". Did you mean "text"?
  ```
- **`ParseError` is now a `Diagnostic`**: every parser raise site carries a
  real column — statement keywords, the missing-`:` position, the offending
  name in `add`/`take`/`ask ... into`, dangling operators (`say 2 +`
  points at the `+`), unclosed `(` / `{`, and bad characters (a lexer
  `LexError` becomes a clean parse error instead of a traceback).
- **`TypeErrorNiko` and `CompileError` are `Diagnostic`s too**: the
  unknown-name error passes the name as a token so the caret spans it;
  other type errors echo the source line; compiler errors (e.g. `stop`
  outside a loop) render through the same formatter instead of a traceback.
- **CLI**: `run` / `check` / `build` / `disasm` / `format` all render the
  new format; exit codes unchanged (1 on error).
- **Tests**: `tests/test_diagnostics.py` now asserts columns, caret spans,
  and source-line echo for parse errors, type errors, and the typo
  suggestion. Note: expression-level carets resolve by searching the
  source line for the offending token (first match), so they can be
  approximate when the same text appears twice on one line.

## WIP10 — typo suggestions in diagnostics

- Unknown identifiers now suggest a close match: the typechecker matches
  a mistyped name against builtin helpers and declared names and reports,
  e.g. `unknown name "totl". Did you mean "text"?`
- Regression test: `tests/test_diagnostics.py`.

## Added
- **Serialized `.nikoir` loader** (first Alpha 5 milestone item). A file
  produced by `niko2 build file.niko` can now be run or disassembled
  directly — `niko2 run file.nikoir` / `niko2 disasm file.nikoir` — without
  the original `.niko` source and without re-parsing or re-typechecking.
  New module: `niko2/nikoir.py` (`save_nikoir` / `load_nikoir`).
- **`tests/run_niko2_tests.py`**: the first regression suite that actually
  exercises the Niko 2 pipeline end to end (parse -> check -> compile ->
  VM), including a build -> load -> run round trip through a `.nikoir`
  artifact. `tests/run_tests.py` only ever tested Niko 1 (`niko.py`); Niko 2
  had no automated regression coverage before this.
- `niko2/KNOWN_LIMITATIONS.md`: documents which Niko 1 statements/helpers
  are not yet in Niko 2, and the bugs listed below.

## Fixed
- Recursive functions crashed with "I don't know what \"NAME\" is." in the
  VM — a closure-construction bug in `VM.execute()`.
- `N of LIST` (list indexing) passed its arguments to `item_of` swapped,
  breaking or corrupting every list-index expression.
- `record.attr` dot-access silently did nothing (dropped the `.attr` part)
  instead of erroring or working — the AST/compiler/VM support existed but
  the parser never produced the node.
- The expression parser silently ignored trailing tokens it couldn't
  parse instead of raising an error (this is what let `.attr` disappear
  unnoticed).
- `niko2`'s CLI always exited `0`, even on errors, because `__main__.py`
  didn't propagate `main()`'s return code — breaking any CI/script relying
  on the exit status of `niko2 check`.

## Verification
- `python tests/run_tests.py` — 26 cases, 0 failures (Niko 1, unaffected).
- `python tests/run_niko2_tests.py` — 4 cases, 0 failures, plus a passing
  `.nikoir` build/load/run round trip.
