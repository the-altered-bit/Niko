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

- **Module-aware diagnostics re-parse modules per edit (Alpha 23).**
  The language server's live analysis follows `import`/`use` through
  the module graph: imported names and `use`-merged names no longer
  get phantom `unknown name` squiggles, unknown attributes on module
  aliases are flagged, and unresolvable imports yield exactly one
  diagnostic on the import line. Single-file documents keep the old
  behavior (no disk IO). With modules, each didChange parses the entry
  + open docs, reads/parses every reachable module, and typechecks
  each unit — no desugaring, no backend compilation. No mtime cache
  yet; future work.
- **`ask` under the debugger needs a cooperating client.** The debuggee's
  stdin is the DAP protocol stream, so the adapter answers `ask` with a
  Niko-specific reverse `input` request to the debug client (prompt in
  the arguments). A client that answers keeps the program going; one
  that errors or stays silent gets a bounded wait (30s, `inputTimeout`
  launch arg) and the program receives `""` with a warning. Stock VS
  Code does not answer reverse `input` requests, so under VS Code `ask`
  currently yields `""` after the timeout.
- **Hover and go-to-definition are line-oriented.** AST nodes carry line
  numbers but not columns, so the language server resolves to the
  nearest sensible line rather than an exact range.
- **The `$match` hidden slot is visible-adjacent.** Match desugaring
  still stores the subject in `$match`; the DAP adapter filters `$`
  names out of the Locals view, but expression evaluation (not yet
  implemented) must not expose it.
- **The VS Code extension is not on the Marketplace.** Install
  `editors/vscode/niko-0.25.0.vsix` via *Extensions: Install from
  VSIX…*. The extension was refreshed in Alpha 25 (grammar current
  through Alpha 25, multi-file debugging, conditional breakpoints,
  evaluate, module-aware diagnostics); the publish runbook is
  `editors/vscode/PUBLISH.md` — publishing needs the user's publisher
  account and token.
- Future debugger work: data breakpoints / logpoints. (The old
  `use`-import item is done — Alpha 22 routes `use` through the module
  pipeline, so breakpoints, stepping, and frames work inside used
  files. The old same-line breakpoint re-fire quirk is fixed too —
  Alpha 23: a breakpoint on a call line fires once, not again when the
  call returns.)

## Alpha 8 WASM backend limits

- **Numbers are f64.** All Niko numbers compile to 64-bit floats; very
  large integers lose precision exactly the way the VM's floats do.
- **`upper`/`lower` are ASCII-only.** Non-ASCII text passes through
  unchanged.
- **`import` and `use` modules work on all backends** (Alpha 13 /
  Alpha 22); see below.
- **No file-I/O builtins** (`read_file`, `write_file`, `append_file`,
  `read_lines`, `file_exists`, `try_read_file`). They raise `CompileError`.
- **Method calls work** (Alpha 13 added `x.method(...)` via the
  first-class callee path).
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
- **Method-call syntax works** (Alpha 13), same as WASM. `use` imports
  work too (Alpha 22) — byte-identical to the VM.
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

## Alpha 13 modules limits

- **The language server follows `import` AND `use` for go-to-definition,
  hover (Alpha 17/21/22), and diagnostics (Alpha 23).**
  `niko2 lsp` jumps from `alias.name` to the top-level `set`/`to` in the
  module file, from an import's path string to the module file itself,
  from a `use`-merged bare name to its `set`/`to` in the used file, and
  from a `use` path string to the used file — resolved through the same
  search path as the compiler (file dir → `NIKO_PATH` → bundled stdlib →
  cwd → package cache). Hover on `alias.name` / used names shows the
  signature and doc comment from the module file. Diagnostics still
  analyze each module file through the module pipeline (Alpha 23),
  with a read-only lint catching unknown attributes on module aliases
  (`import` aliases check as `map` so nothing breaks). Type errors in
  module files attribute to the module's own file and line, and
  unsaved module buffers override the on-disk version.
- **Module search path exists; the package manager shipped in Alpha 16.**
  `import` resolves: importing file's dir → `NIKO_PATH` dirs → bundled
  stdlib (`stdlib/…` paths) → cwd; a local/`NIKO_PATH` `stdlib/` tree
  shadows the bundled one. `pkg:<name>/…` imports resolve from the
  package cache (`~/.niko/packages`, see below).
- **`use` is rejected inside imported modules.** The entry file may still
  use `use "…"`; imported modules must use `import "…" as …`.
- **`import` must be at the top of the file** — not inside a `to` or a
  block (checker error: `import must be at the top of the file`).
- **`use` must be at the top of the file** — not inside a `to` or a
  block (checker error: `'use' is only allowed at the top of a file,
  not inside a function`).

## Alpha 19/24 package manager limits

What's fixed (Alpha 24):

- **Transitive dependencies are pinned in the lockfile.** `niko2 lock`
  writes `{version, source, range}` for every transitive dependency
  (`range` = the parent package's requested range); re-locking never
  silently upgrades; `get --update` re-resolves and re-pins the updated
  package's subtree.
- **Locked reinstall.** After every registry `get`, the CLI installs any
  lockfile-pinned (registry-source) versions missing from the cache,
  exactly as pinned — strictly additive, never downgrades/removes.
- **HTTP(S) registries work**, with a 10s fetch timeout and plain-English
  errors for HTTP status / DNS failure / connection refused / timeout.
  Downloads are verified (sha256) before the cache is touched, so a bad
  download can never leave a half-installed package.

What remains:

- **No auth, no remote publish.** Registries can be local directories or
  remote index URLs, but `niko2 publish` only publishes to local
  directory registries — publishing to a remote registry is refused
  with "publishing needs auth, which is not supported yet". There is
  no registry account model at all. (A registry is just static files —
  `index.json` + `tarballs/`; publish locally, then sync the directory
  to any static file host. See `ALPHA24_DESIGN.md` "Static-hosting
  recipe".)
- **A `pkg:` import *inside* an installed package resolves to the newest
  *cached* version, not the lockfile pin.** `locked_package_versions`
  walks up from the importing file; inside the cache directory there is
  no `niko.lock`, so nested imports see the newest cached version.
  Direct `pkg:` imports from your project always honor the pin
  (pre-existing Alpha 16 behavior; pinned by a test in
  `tests/test_registry.py`).
- **No version-range solving at import time.** `import "pkg:…"` takes no
  version — the `niko.lock` pin decides, with the newest cached version
  as fallback when there is no lockfile. The solver runs only in
  `niko2 get` / `niko2 get --update`, which guarantee a satisfying
  version is cached.
- **A bare local directory named exactly like a package is treated as a
  registry lookup.** Spell it `./dir` to force directory handling
  (`niko2 get ./mydir`).
- **`niko2 get` and `niko2 publish` are the only commands that use the
  network.** Compile/run/check/wasm/native/debug/lsp all resolve
  packages offline from `~/.niko/packages` (override with
  `NIKO_PKG_CACHE`). A pinned-but-evicted package (lock says 9.9.9,
  only 1.0.0 cached) is an explicit error, not a silent fallback.
- **The cache is a plain directory copy.** To pick up upstream changes
  from a git source, run `niko2 get <url> --force` again (re-locking
  keeps the old pin if you don't want the new version — `niko2 lock`
  never silently upgrades).
- **No `niko` command alias.** The command is `niko2 get`; a bare `niko`
  script was deliberately not added to avoid ambiguity with Niko 1's
  `niko.py` at the repo root.
- **Flags after positionals:** `niko2 get <src> --force` works;
  `niko2 get --force <src>` is rejected (pre-existing argparse quirk).
  Exception: `niko2 get --update <name>` is special-cased via an argv
  pre-scan, so both flag positions work for `--update`.

## Alpha 20 REPL limits

- **VM backend only.** The REPL (`niko2 repl`) runs on the VM. The WASM
  and native backends are compile targets, not REPL targets.
- **Diagnostics show cumulative session line numbers**, not per-chunk
  ones — a caret points at the line you typed, counted from the start
  of the session.
- **The block-open colon is a heuristic.** A stripped line ending in
  `:` opens a block unless the colon is inside a string literal, and
  "inside a string" is decided by quote parity with escapes honoured,
  not full string parsing.
- **Multi-line constructs need increasing indentation; spaces expected.**
  The REPL cannot know a block is closed except by a blank line or a
  dedent, so tabs or flat multi-line pastes may misbehave.
- **`:undo` drops the last chunk; there is no redo.** Undo rebuilds
  the session by re-running the kept chunks on a fresh VM, so
  interpreter state is rewound but side effects outside the VM are
  replayed, not undone: a kept chunk that writes a file writes it
  again, and a kept chunk using `ask` prompts again during the
  rebuild. A chunk that parsed and typechecked still joins the
  history even if it failed at runtime (script-that-crashed
  semantics).
- **Redefining a function replaces its nested helpers session-wide.**
  A reference to the old function saved in an earlier chunk resolves
  nested names to the newest definitions.

## Alpha 27 test runner limits

- **VM backend only.** `niko2 test` runs on the VM — it is a dev tool
  and the VM is the reference semantics. WASM/native are compile
  targets; per-test toolchain spin-up would be slow and cannot easily
  invoke individual test functions.
- **`assert` is not supported on the native backend.** Compiling a
  program containing `assert` with `niko2 native` is a clean compile
  error (`assert is not supported on the native backend`), never a
  miscompile. VM and WASM implement it with identical messages.
- **No fixtures, mocks, or coverage.** The runner discovers, isolates,
  and reports — nothing more. These are future work, not half-built
  flags.
- **Only `to test_<name>:` is collected.** `to test <name>:` (space
  form) parses as a function literally named `'test <name>'`, which no
  call syntax can invoke — it is dead code and the runner ignores it.
- **`say` output prints straight through** during tests; there is no
  output-capture assertion API.

## Alpha 28 dogfood notes

Building `examples/ssg` (a static site generator written in Niko) found
three language bugs, all fixed with regression tests in
`tests/test_dogfood.py`:

- **Sequential `if`s were parsed as one if/elif chain** (`niko2/parser.py`).
  `parse_if`'s continuation loop matched any later `if ` line, so in
  `if a: ...` / `if b: ...` the second body never ran when the first
  condition was true. Only `otherwise if` / `otherwise:` at the same
  indent continue an `if` now; a later bare `if` always starts a new
  statement.
- **Forward references failed inside imported modules**
  (`niko2/modules.py`). Only the entry script pre-defined its own
  top-level names; a module function could not call another defined
  later in the same file. Every unit now pre-defines its own top-level
  names before checking.
- **Closures leaked builtins past shadowing bindings**
  (`niko2/closures.py`). A nested function reading a builtin-*named*
  variable bound by an enclosing function (a local `set text to ...`,
  or an `import ... as text` alias inside a module wrapper) received the
  builtin instead of the binding. Builtin names bound by an enclosing
  function are now captured like any other local. The Alpha 10 pinned
  rule is unchanged: a builtin name in *direct call position* still
  means the builtin.

What remains (known limits, not bugs):

- **Alpha 28 dogfood note, fixed in Alpha 30:** `and` / `or` used to
  evaluate both operands on all three backends (a real semantic
  divergence from Niko 1) — guard idioms like
  `if i < n and item (i + 1) of s is "*":` raised an index error when
  the guard was false. Alpha 30 implements Niko 1's rule on
  VM/WASM/native: short-circuit evaluation with operand-returning
  semantics (falsy = `no`, `nothing`, `0`, `0.0`, `""`, `[]`, `{}`;
  everything else truthy). Historical note: the fix also required
  a constant-pool dedup fix in `niko2/ir.py` (`True`/`1` used to
  collide) and a latent native `while`-condition fix (conditions are
  now evaluated inside the loop, not once before it).
- **Runtime errors in imported modules report the entry file's path.**
  Check-time errors are re-tagged with the defining module's path, but
  a runtime failure inside a module says e.g. `Niko error in main.niko`
  with the module's line number. The DAP adapter already maps frames
  to files; `niko2 run`'s error printer does not yet.

## Alpha 29 playground notes

`examples/playground/` runs Niko 2 in the browser (Pyodide + WASM).
Browser differences from `niko2 run`, all deliberate:

- **File builtins fail at compile time** with a clean Niko error —
  the browser build has no filesystem for the guest.
- **`ask` uses the blocking browser `prompt()`** (headless browsers
  may auto-dismiss it; the program then sees an empty answer).
- **`sleep(x)` is a capped busy-wait (max 2 s)** — it blocks the tab.
- **No `pkg:` imports** — no network, no registry in the browser.
  `import "stdlib/....niko"` works (stdlib ships inside the bundle).
- **Programs are in-memory only** — nothing persists across reloads.
- **An infinite loop hangs the tab** — WebAssembly can't be
  pre-empted; close/reload to recover.
- The in-browser end-to-end test (`e2e.mjs`) could not run on the
  build machine (Pyodide CDN unreachable from it); one networked run
  is still owed before the page is called proven.

## Alpha 31 json notes

`niko2/stdlib/json.niko` (pure Niko, no backend changes) ships with
three documented module limitations and two native-backend bugs worked
around in pure Niko, plus one new WASM backend bug found by testing.
All are pinned by `tests/test_json.py`.

**Module limitations** (in the module header and `STDLIB.md` too):

- **`\uXXXX` escapes above U+007F are a parse error** naming the
  position (`\u0041`–`\u007f` decode fine). Raw UTF-8 in JSON strings
  parses on VM/native; on WASM it fails (backend bug, below).
- **Numbers with magnitude >= 1e15 are rejected** with
  `number out of range`: the VM keeps integers exact while WASM and
  native use f64, so the module rejects uniformly instead of
  disagreeing.
- **Integer-valued floats render `1.0` on the VM but `1` on
  WASM/native** (each backend's own `text()` rule); both are valid
  JSON for the same value.

**Native backend bugs, worked around in pure Niko** (no backend
changes; remove the workarounds if the backend is fixed):

- **Native `otherwise if` + short-circuit `and`/`or` returns
  `nothing` when the first operand doesn't decide.** Workaround: the
  module splits such branches into nested `if`s instead of
  `otherwise if` chains.
- **Native hoists call arguments in `if`/`while` conditions past
  short-circuit guards.** Workaround: `_char_at` is total (returns
  `""` past the end, never panics) and call sites never rely on a
  guard to prevent the call.

**WASM backend bug (new in Alpha 31, FIXED in Alpha 33): `while yes:` +
string indexing on multibyte text miscompiled.** Root cause: in
`niko2/backends/wasm.py`, `_b_item_of`'s text branch overwrote the
byte-length local with the char count from `utf8_len`, then passed it as
the *byte* length argument to `utf8_byte_offset`. For ASCII the two are
equal, so nothing showed; for multibyte text the byte scan stopped early
and the end offset of the final character(s) came back truncated, so
`item_of` on the last character returned `""`. In a `while yes:`
scan-to-terminator loop the terminator never matched, the loop ran past
the end, and the host panicked with `Line 4: string index out of range`.
Fix: keep the byte length in its own local (`blen`) and pass that to
`utf8_byte_offset`. All other `utf8_byte_offset` call sites already kept
the two separate. Pinned by `tests/test_wasm_unicode.py` (20 cases,
3-way differential) and the flipped WASM assertion in
`tests/test_json.py`. Minimal repro (now prints `héllo` on all three
backends):

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
    give back out
say f2("\"héllo\"", {pos: 2})
```

VM/native print `héllo`; before the fix, WASM died with `Line 4: string
index out of range` from the host. (The same loop with a bounded `while`
condition appeared to work only because the truncated end offset still
landed inside the string for non-final characters; ASCII strings were
unaffected everywhere since char count equals byte length.) In
`json.niko` this surfaced as `json.parse` failing on JSON strings
containing raw multibyte UTF-8 (`json parse error at character 14:
unterminated string` for `"héllo wörld"`), while `json.stringify` of
multibyte text was correct on WASM. `tests/test_json.py` asserted the
WASM failure explicitly (commented "flip when fixed"); the assertion is
now flipped to success, and `tests/test_wasm_unicode.py` pins the fix
3-way.

## Niko 1 -> Niko 2 divergences found during the Alpha 35 audit (2026-09-22)

A line-by-line audit of `niko.py` against `niko2/`, verified with ~70
differential probes on the VM, turned up three divergences that were not
previously documented. All three are covered in `MIGRATION_GUIDE.md` and
detected by `niko2 migrate`.

1. **`use <python-lib>` is silently ignored.** Niko 1's `use math` means
   `import math`. Niko 2's `use` only includes other Niko files, so
   `use math` parses and typechecks cleanly but imports nothing -- the
   program then fails later with `unknown name` (or worse, silently does
   without the library). This is the most dangerous item in the
   migration guide precisely because nothing fails at the `use` line.
   `niko2 migrate` reports it as `[use-python]` (an error), with a
   per-library replacement suggestion.
2. **Functions cannot write top-level variables.** Niko 1 functions wrote
   through to top-level variables (`globals()["x"]`); on Niko 2, `set` /
   `ask ... into` / `for each` / `add ... to` / `take ... from` inside a
   function always create or update a function-local. The top-level
   variable silently keeps its old value -- verified: Niko 1 prints `7`,
   Niko 2 prints `0` for `tests/cases/globals.niko`. In-place mutation
   (`put`, `remove`, `set item`, indexed `set`) still reaches the shared
   object in both. `niko2 migrate` reports this as `[global-write]`
   (a warning).
3. **`result` truthiness across backends (unverified).** The audit
   flagged a possible VM/native split in how `result` values behave in
   boolean position, but this was not confirmed differentially -- it
   needs a dedicated probe before it can be documented as fact.

## Native: `otherwise if` with a temp-emitting condition miscompiled (Alpha 36, fixed in Alpha 38, 2026-09-23)

Found by the `niko2 fuzz` differential campaign on its first real run
(seed 20260923, case 10). The native backend's `IfStmt` codegen called
`gen_expr(cond)` *before* emitting the `else if` line, so temp-variable
statements landed between the previous branch's closing `}` and the
`else if` -- invalid C (`'else' without a previous 'if'`). Worse, the
`and`/`or` lowering's own `if (!t) { t = rhs; }` captured the chain's
`else if` (dangling else) and silently ran the wrong branch.

Fixed in Alpha 38: `_gen_if_chain` in `niko2/backends/native.py` nests
each branch after the first inside the previous branch's `else { ... }`
block, so temps land legally and the dangling-else capture is
impossible; conditions still evaluate lazily in order. Regression
proof: `tests/test_native_otherwise.py` (13 three-way differential
cases, both original repros included). `tests/test_fuzz.py` runs native
again (sampled 1-in-10). See `RELEASE_NOTES_ALPHA38.md` /
`ALPHA38_DESIGN.md`.

## `ask` past stdin EOF diverges across backends (Alpha 36, 2026-09-23)

Found by the `niko2 fuzz` pilot (seed 1, case 6: an `ask number` inside a
`for each` loop consumed more stdin lines than were provided). Minimal
repro (stdin holds one line, `42`):

```
ask number "n? " into x
ask number "m? " into y
say x, y
```

| backend | text `ask` at EOF | `ask number` at EOF |
|---|---|---|
| VM | rc=1, `Niko error ...: EOF when reading a line` | rc=1, same error |
| WASM | rc=0, yields `""` silently | **hangs**: prints `Please type a number.` forever |
| native | rc=0, yields `""` silently | **hangs** (timeout) |

So on exhausted stdin the VM raises a clean error, WASM/native
silently produce an empty string for text asks, and both compiled
backends spin forever on `ask number`. The WASM/native number-ask hang
is the serious half: an unattended program waiting on a closed pipe
never terminates. Not fixed in Alpha 36 (no language-semantics changes
this sprint); the fuzzer avoids the whole class by construction --
`ask` is never generated inside a loop or function body, so the runtime
ask count can never exceed the canned stdin lines
(`niko2/fuzz.py`, "Termination by construction").

## `try_number` text-failure message differs on compiled backends (Alpha 36, 2026-09-23)

Found by the `niko2 fuzz` differential campaign (seed 201, case 140:
a `match` arm compared `error_message(try_number("xyz!"))` and the
first output line differed VM-vs-WASM). Minimal repro:

```
match try_number("xyz!"):
    when error m:
        say m
    otherwise:
        say "not-error"
```

| backend | output |
|---|---|
| VM | `I can't turn 'xyz!' into a number.` |
| WASM | `xyz!` |
| native | `xyz!` |

The VM is the reference: the WASM codegen comment
(`niko2/backends/wasm.py`, `try_number`) explicitly says the intent is
`"I can't turn '<s>' into a number." (exact VM message)`, but the
text-parse-failure path emits `make_result(s, 0)` -- the raw input
string as the error payload -- instead of building that message (the
non-text path right below does build it, and agrees with the VM).
The native runtime (`niko2/backends/niko_runtime.c`, `b_try_number`)
then copied the buggy behavior deliberately
(`return nval_error(x);  /* WASM parity: the text itself is the message */`),
cementing the divergence from the VM. Only the text-failure path is
affected: `try_number("3.5")` succeeds and `try_number([1, 2])` fails
with `I can't turn [1, 2] into a number.` identically on all three.

Not fixed in Alpha 36 (backend-sprint work: the WASM text branch needs
the same `concat3` message construction the fallthrough branch uses,
and the native runtime needs to match; the "WASM parity" comment goes
away). The fuzzer avoids the shape by construction (`niko2/fuzz.py`
generates `try_number` only with valid numeric inputs): the message
stays observable through string operations on the bound error (e.g.
seed 101 case 507 printed `replace(m, " ", "_")`), so no output
normalization could allowlist every derived form. The runner's
`EXPECTED_DIVERGENCES` still carries a `try-number-message` entry for
the direct form, which fires on `--corpus` re-runs of saved cases.

## Native: `ask number` doesn't re-print the prompt on reprompt (Alpha 36, 2026-09-23)

Found by the `niko2 fuzz` differential campaign (seed 201, case 22).
Minimal repro (stdin holds `gamma` then `7657`):

```
ask number "city? " into v8
say "got", v8
```

| backend | output |
|---|---|
| VM | `city? Please type a number.` / `city? got 7657` |
| WASM | `city? Please type a number.` / `city? got 7657` |
| native | `city? Please type a number.` / `got 7657` |

After a non-numeric line, the native backend prints `Please type a
number.` but does not re-display the `city? ` prompt before reading
the next line; the value is still read correctly (`7657`). VM and WASM
re-print the prompt. Cosmetic-only (no wrong value, no hang), but it is
a real stdout divergence the differential campaign flags. Not fixed in
Alpha 36 (backend-sprint work, next to the `ask`-at-EOF entries above).

## VM: `stop` out of `repeat`/`for each` leaked the loop iterator (Alpha 36, fixed in Alpha 37)

Found by the `niko2 fuzz` differential campaign (seed 777, case 2 --
a vm-only hang; WASM and native agree and terminate). Minimal repro
(now the regression proof):

```
repeat 1 times:
    for each it22 in [-573 % 3, -736]:
        stop
say "done"
```

VM hung forever; WASM and native printed `done`. Root cause
(`niko2/compiler.py` + `niko2/vm.py`): `repeat` and `for each` drive
iteration from a per-frame `iter_stack` (`ITER_REPEAT`/`ITER_PREP` push
an iterator; the `..._NEXT` op pops it on exhaustion). `stop` compiled
to a plain `JUMP` to the innermost loop's end address -- the iterator
was never popped. The stale iterator then corrupted the next enclosing
`repeat`/`for each` loop's `..._NEXT` op, which consumes from
`iter_stack[-1]`: in the repro the outer `repeat`'s `REPEAT_NEXT`
kept finding the inner `for each`'s unexhausted iterator instead of
its own, so the repeat never advanced and the program looped forever
(and leaked one iterator per cycle). With different shapes the same
bug silently yielded *wrong iteration values* instead of hanging --
e.g. a `for each` inside a `while` inside a `for each` resumed the
outer loop from the inner loop's leftover iterator.

**Fixed in Alpha 37:** the compiler's `loop_stack` now records each
loop's kind (`'repeat'`/`'for'`/`'while'`), and `stop` targeting a
`repeat`/`for each` emits a new `ITER_POP` opcode before the break
jump (the normal-exhaustion paths already pop, so the pop belongs on
the break path only). `skip` is unchanged (it jumps to the loop head,
where the live iterator is correctly advanced) and `stop` out of a
`while` emits no pop. WASM and native never had the bug (no iterator
stack) and are untouched. The fuzzer's `_gen_loop_exit` restriction
that dodged these shapes is lifted, and `tests/test_fuzz.py` now pins
that nested `stop`s are generated. Regression coverage:
`tests/test_stop.py` (31 three-way differential cases + 2
compile-error cases) plus a 3,000-case focused fuzz campaign with
zero VM-vs-WASM divergences.

`skip` was always unaffected (it jumps to the loop head, where the
live iterator is correctly advanced). `stop` out of a `while` loop
was always unaffected (no iterator involved), as was `stop` out of a
`repeat`/`for each` with no `for each`/`repeat` above it (the leaked
iterator was never consumed).

## VM vs WASM/native: comparing `yes`/`no` with numbers (Alpha 36, 2026-09-23)

Found by the `niko2 fuzz` differential campaign (seed 101, case 178).
Minimal repro:

```
say min(-733, no)
say no < 5
```

The VM prints `-733` / `yes`; WASM and native raise
`I can't compare those values.` (rc=1). Root cause: the VM implements
`min`/`max` as Python's `min`/`max` (`niko2/vm.py`) and its comparison
ops inherit Python's `bool < int` ordering (`no`/`yes` behave as 0/1),
while the WASM and native backends reject mixed bool/number operands
in `<`, `>`, `<=`, `>=`, `==`, `!=` (and therefore in `min`/`max`,
which compare). The typechecker accepts these programs, so this is a
genuine semantic divergence, not a rejected program. Not fixed in
Alpha 36 (deciding whether `yes`/`no` should order as 0/1 is a
language-semantics question, out of sprint scope). The fuzzer no
longer generates the shape: its expression generator never mixes
bool with number operands, and a generator type-model bug that let a
bool-returning call slip into an `int` slot (early `give back` in a
function body used a random type instead of the function's return
type) is fixed (`niko2/fuzz.py`, `self.func_ret`), pinned by
`tests/test_fuzz.py`.
