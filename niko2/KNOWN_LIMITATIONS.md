# Niko 2 (Alpha 4/5): known gaps vs. Niko 1

This file exists because it wasn't obvious from the release notes or the
design docs how much of the Niko 1 language Niko 2 actually supports yet.
Running the existing `tests/cases/*.niko` (Niko 1 regression cases) through
`niko2 run` shows most of them fail — not because of bugs, but because the
statements/expressions/helpers below simply aren't implemented in Niko 2's
parser or VM yet. Anyone picking up this project (human or AI) should know
this going in, so they don't assume Niko 2 == Niko 1 syntax plus types.

## Statements Niko 2's parser does not support yet
(none remaining from the original Niko 1 statement list — `ask`, `ask number`,
and `numbers A to B` were the last ones; see "Added" section below)

## Statements added since the gap was first documented (Alpha 5, in progress)
`put VALUE in LIST`, `remove VALUE from LIST`, `add VALUE to NAME`,
`take VALUE from NAME`, `set item N of LIST to VALUE`,
`set RECORD["key"] to VALUE` / `set LIST[n] to VALUE`,
`ask PROMPT into NAME`, `ask number PROMPT into NAME`, and the
`numbers A to B` range expression are now implemented (parser + typecheck +
compiler + VM: new `STORE_INDEX`, `LIST_APPEND`, `LIST_REMOVE`, `INPUT`
opcodes, plus a `niko_range` builtin). See `tests/niko2_cases/mutation.niko`
and `tests/niko2_cases/ask_and_range.niko`.

Niko 2's statement-level syntax is now at parity with the Niko 1 statement
list in `NIKO_AI_BRIEF.md` section 3. What's still missing is entirely on
the standard-library and type-system side (below).

## Standard-library helpers (gap closed in WIP12)

The Niko 1 helpers `starts_with`, `ends_with`, `count_of`, `pick`,
`write_file`, `append_file`, `read_file`, `read_lines`, `file_exists` are
now implemented in Niko 2: builtins in `niko2/vm.py` and `niko2/runtime.py`,
names registered in the typechecker, and a new `files` group in
`niko2/stdlib.py`'s module API. Tested by `tests/niko2_cases/stdlib_text.niko`
and `tests/test_stdlib.py`.

**Files decision:** Niko 2's file helpers touch **real files**, with relative
paths resolved against the process working directory — matching Niko 1's
desktop engine (`niko.py`). The browser IDE's virtual (localStorage) files
remain that backend's own behavior. If a future sandboxed or embedded Niko 2
backend needs virtual files, it should go through the `niko2/stdlib.py`
module API (`module_symbols('files')`), which is the seam for swapping the
implementation.

## Type system
- `list<T>` generics are checked for real since Alpha 6 WIP1: list literals
  infer an element type (`[1, 2, 3]` is `list<number>`, nested literals nest
  accordingly), annotations are enforced element-by-element against
  literals (`set xs: list<number> to ["a"]` fails), `put` checks the value
  against the list's element type, `X[i]` / `item_of(i, X)` yield the
  element type, and `for each x in XS` types `x` as the element type.
  Heterogeneous literals (`[1, "a"]`) stay legal and infer `list<any>`.
  Nested annotations like `list<list<number>>` parse.
- Not yet generic-aware: builtin *return* types (`sorted`, `unique`, …)
  still return `any`, and call arguments are not checked against parameter
  annotations. `map<K,V>` has no value-type inference (records infer plain
  `map`). These are future work.

## Error model and pattern matching (Alpha 6 WIP2/WIP3, complete)

- `option<T>` (a `T` or `nothing`; a plain `T` is a valid `option<T>`) and
  `result<T>` (`ok(value)` / `error(message)`, error side always text).
  Builtins: `ok`, `error`, `is_ok`, `is_error`, `unwrap`, `unwrap_or`,
  `error_message`, `try_read_file`, `try_number`. The raising builtins
  (`read_file`, `number`) are unchanged; `try_*` is the opt-in
  value-based path. `unwrap` on an error re-raises the message itself.
- `match` statement with `when` arms and optional `otherwise:`.
  Patterns: literals, `ok name` / `error name` destructuring, bare-name
  bindings, comma-separated alternatives, **guards** (`when PATTERN if EXPR:`),
  **list patterns** (`[a, b]`, `[]`, `[first, ...rest]`), and **record
  patterns** (`{name: n, age: a}`), nested arbitrarily. First match wins;
  the subject is evaluated once; per arm the order is pattern test →
  bindings → guard → body, and a failed guard falls through like a failed
  match. No match + no `otherwise` just continues.
  The checker types bindings (list elements from `list<T>`, `...rest` as
  `list<T>`) and rejects list/record/`ok`/`error` patterns against
  statically known wrong types. The compiler desugars to jumps plus
  `is_ok`/`unwrap` calls and three tiny opcodes (`IS_LIST`, `IS_RECORD`,
  `LIST_SLICE`) — no new VM opcodes for the classic patterns.
- `match` as an expression (Alpha 12, complete): `set x to match v:`,
  `give back match v:`, `say match v:` — the winning arm's final
  expression is the value (new `MatchExpr` AST node). The checker requires
  exhaustiveness (`otherwise:` or an unguarded catch-all `when x:` last
  arm) and arm-type agreement; each arm body must end with an expression.
  VM uses a hidden `$matchval` slot; WASM/native use fresh result
  locals/temps. Match statements keep their lenient no-match behavior.
- Not yet: `some()` constructor (options are just `T | nothing`).
- Caution: the compiler stores the match subject in a hidden `$match`
  slot (nested pattern subjects in `$patN` slots). User code cannot name
  `$`, so this is safe, but a future backend should use real temp
  registers instead of magic names.

## What *does* work today (see tests/niko2_cases/)
`set` (typed and untyped), `say`, `if`/`otherwise if`/`otherwise`,
`repeat N times`, `while`, `for each ... in`, `to ... with ... -> type`
(including recursion — see "Bugs fixed" below), `give back`, lists,
records, indexing (`X[i]`, `N of LIST`), attribute access (`record.key`),
list/record mutation (`put`/`remove`/`add ... to`/`take ... from`/indexed
`set`), `ask`/`ask number ... into`, `numbers A to B`, the operators and
comparisons from `NIKO_AI_BRIEF.md` section 3, and the `item_of`/math/text/
list builtins wired into `niko2/vm.py`.

## Bugs found and fixed while working on this (see git history / commit
notes for exact diffs)
1. **Recursion was broken in the VM.** `VM.execute()` built each function's
   closure from `env.copy()` taken *before* that function's own name (and
   any functions after it) were added to `env`, so a function could never
   see itself or later sibling functions. Fixed by sharing the live `env`
   dict as the closure instead of copying it.
2. **`N of LIST` passed arguments to `item_of` in the wrong order**,
   e.g. `2 of nums` called `item_of(nums, 2)` instead of `item_of(2, nums)`,
   producing a runtime crash or nonsense result for any list-index
   expression. Fixed in the parser's postfix-`of` handling.
3. **`record.attr` syntax was silently a no-op.** `AttrExpr` existed in the
   AST/compiler/VM/typechecker but the parser never produced one — the `.`
   token just wasn't handled in the expression parser's postfix loop, so
   `person.age` parsed as just `person` and the `.age` was dropped with no
   error. Fixed by adding a `.` case to the postfix loop, and (separately)
   by making the expression parser raise a `ParseError` on any leftover
   unconsumed tokens instead of silently ignoring them — this is what let
   `.age` disappear without complaint in the first place.
4. **`niko2`'s exit code was always 0**, even on parse/type/runtime errors,
   because `niko2/__main__.py` never passed `main()`'s return value to
   `sys.exit()`. This would silently break any CI/script that checked
   `niko2 check`'s exit status. Fixed.
5. **A working `if` statement dispatch line was accidentally deleted**
   while inserting the new `ask`/`ask number` statement branches into
   `parse_stmt` (a `str_replace` used a one-line anchor and the
   replacement forgot to include it back). Every `if ...:` line then fell
   through to the generic expression-statement branch — which, thanks to
   fix #3's "no silently-dropped tokens" change, failed loudly with a
   parse error instead of silently misbehaving, and was caught immediately
   by `tests/run_niko2_tests.py`. Restored. **Lesson for future edits to
   `parser.py`:** `parse_stmt`'s `if text.startswith(...)` chain is a flat
   sequence of independent branches — always view the surrounding lines
   after any edit near it (or just re-run the test suite) rather than
   trusting a `str_replace` diff alone, since a dropped branch fails
   silently-ish (a confusing downstream parse error, not an obvious
   traceback pointing at the real cause).

## Recommended before adding more Alpha 5 features
Given the gaps above, consider whether closing them (a real regression
suite already exists now: `tests/run_niko2_tests.py`) is higher priority
than new Alpha 5 items like the formatter or richer diagnostics — a
formatter/diagnostics tool is much more useful once the language surface
it's formatting/diagnosing is closer to feature-complete.

## Alpha 7 tooling limits

- **Debugger runs single-file programs.** `use` imports are not loaded
  under `niko2 debug`; a program with `use` lines will fail to check.
- **`ask` doesn't work while debugging.** The debuggee's stdin is the
  DAP protocol stream, so `ask` would consume protocol bytes. (Not yet
  guarded with a friendly error — it will simply misbehave.)
- **Hover and go-to-definition are line-oriented.** AST nodes carry line
  numbers but not columns, so the language server resolves to the
  nearest sensible line rather than an exact range.
- **The `$match` hidden slot is visible-adjacent.** Match desugaring
  still stores the subject in `$match`; the DAP adapter filters `$`
  names out of the Locals view, but expression evaluation (not yet
  implemented) must not expose it.
- **The VS Code extension is not on the Marketplace.** Install
  `editors/vscode/niko-0.7.0.vsix` via *Extensions: Install from
  VSIX…*.
- Future debugger work: expression evaluation (`evaluate` request),
  conditional breakpoints, `use`-import support, and a friendlier
  `ask`-under-debugger story.

## Alpha 8 WASM backend limits

- **Numbers are f64.** All Niko numbers compile to 64-bit floats; very
  large integers lose precision exactly the way the VM's floats do.
- **`upper`/`lower` are ASCII-only.** Non-ASCII text passes through
  unchanged.
- **No `use` imports.** Multi-module programs can't compile to WASM yet.
- **No file-I/O builtins** (`read_file`, `write_file`, `append_file`,
  `read_lines`, `file_exists`, `try_read_file`). They raise `CompileError`.
- **No method-call syntax** (`x.method(...)`); use the builtin form.
- **Closures and first-class functions work** (Alpha 10): nested `to`
  captures enclosing locals by reference, `set f to add` aliases functions,
  functions pass as arguments, return from functions, and live in
  lists/records — byte-identical to the VM (`tests/test_wasm.py` runs the
  8 canonical closure programs differentially).
- **`ask` needs a host.** Under node it reads stdin; other runtimes must
  provide the `niko_input` import.
- **`now()`/`today()`/`random_int` come from the host** environment.
- Host shim: `niko2/backends/wasm_host.cjs` (node.js).

## Alpha 9 native backend limits

The native backend (`niko2 native`) compiles to C and then to a real
executable with the system C compiler (`niko2/backends/niko_runtime.c` +
generated code). Same value model as WASM (boxed values, f64 numbers).

- **Numbers are f64**, printed with shortest-round-trip formatting so
  `say` output matches the VM exactly.
- **`upper`/`lower` are ASCII-only**, like WASM.
- **No `use` imports**, no method-call syntax — same as WASM.
- **Closures and first-class functions work** (Alpha 10): nested `to`
  captures enclosing locals by reference; functions are values (aliases,
  arguments, return values, list/record members) — byte-identical to the
  VM (`tests/test_native.py` runs the 8 canonical closure programs
  differentially).
- **The VM's constant-pool dedup bug is not mirrored.** Native output is
  correct where the VM prints `yes` as `1`.
- **Panic behavior:** runtime errors print `Niko error at line N: msg` to
  stderr and exit 1; the VM prints its own format and exits 0.
- **`ask` and file-I/O builtins work** (stdin, libc stdio, cwd-relative) —
  these are native-only extras over WASM.
- Needs `cc`/`gcc`/`clang` on PATH; output is a platform executable
  (no extension on POSIX, `.exe` on Windows).
