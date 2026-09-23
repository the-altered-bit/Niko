<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo.png">
    <img src="assets/niko-owl-360.png" alt="Niko logo: a geometric blue owl with yellow eyes" width="200">
  </picture>
</p>

# Niko

A programming language that reads like English.

```
ask "What is your name? " into name
say "Hello,", name + "!"
repeat 3 times:
    say "Niko is easy."
```

## Try it

- **Browser (nothing to install):** open `ide/niko-ide.html` in any browser, or host it as a static page.
- **Desktop:** `python niko.py examples/hello.niko` (Python 3.8+). Run `python niko.py` alone for the REPL.

## What is in this folder

| Path | What |
|---|---|
| `niko.py` | Desktop engine (Niko to Python) with REPL |
| `ide/niko-ide.html` | Browser IDE and engine (Niko to JavaScript), single file |
| `docs/niko-guide.html` | The full guide, A to Z, with a 4-week learning plan |
| `examples/` | Small programs to read and change |
| `tests/` | Test cases that both engines must pass: `python tests/run_tests.py --ide` |
| `assets/` | Logo files (full logo for dark backgrounds, owl-only icons, and `social-preview.png` for GitHub) |
| `NIKO_AI_BRIEF.md` | Everything an AI (or a new contributor) needs to learn or extend Niko |

## Contributing

Read `NIKO_AI_BRIEF.md` section 7. The rule: every change goes into both engines, with a test case.

## License

Not chosen yet. Add a LICENSE file before sharing publicly (MIT is a common, simple choice for a language project).

## Niko 2 Alpha 3 — IR + Bytecode VM

Niko 2 now has a compiler stage and stack VM. Use:

```bash
python -m niko2 run examples/hello_v2.niko
python -m niko2 check examples/hello_v2.niko
python -m niko2 disasm examples/hello_v2.niko
python -m niko2 build examples/hello_v2.niko
```

`build` produces a `.nikoir` artifact for inspection. The VM is the bootstrap execution backend; it does not transpile Niko to Python.

## Niko 2 Alpha 4 — Projects + VM Modules + Standard Library foundation

Alpha 4 adds a project manifest (`niko.toml`), project discovery, `niko2 init`, `niko2 info`, and VM-native module loading. Modules are compiled to Niko IR and executed in a shared VM environment rather than falling back to the legacy Python runtime.

```bash
python -m niko2 init myapp
python -m niko2 info examples/hello_v2.niko
python -m niko2 run examples/hello_v2.niko
```

## Niko 2 Alpha 20 — interactive REPL

`niko2 repl` starts a live session on the VM: type Niko 2 code line by
line, define functions across multiple lines, import modules, and see
bare expressions echoed back — with the same caret diagnostics as
scripts.

```bash
python -m niko2 repl
```

```console
niko> set x to 5
niko> say x + 1
6
niko> to add with a, b:
....     give back a + b
....
niko> say add(2, 3)
5
niko> 1 + 2
3
```

A line ending in `:` opens a block (`.... ` prompt); finish it with a
blank line or a dedented line. A bare expression is evaluated and its
value printed (`nothing` is never echoed). `import "path/to/file.niko"
as alias` works as in scripts — relative paths resolve against the
directory where you started the REPL, and each module initializes once
per session. Commands: `:help`, `:quit` / `:exit`, `:reset` (forgets
everything), `:undo` (drops the last chunk, as if it was never typed).
Ctrl-D leaves; Ctrl-C discards the current block.

## Niko 2 Alpha 23 — module-aware editor diagnostics

The language server's live diagnostics now understand multi-file
programs: `import "lib/util.niko" as util` / `util.shout("hi")` no
longer gets phantom `unknown name` squiggles, nor do names merged in
by `use`. An unknown attribute on a module alias (`util.nope`) is
flagged on the attribute's line, an unresolvable import produces
exactly one diagnostic on the import line, and type errors point at
the module file's own line — not the entry's. Unsaved module buffers
override the on-disk version, so fixes show up before you save.
Single-file documents keep the old zero-disk-IO behavior.

Also in this alpha: a breakpoint on a call line no longer stops twice
(the debugger's old same-line re-fire is fixed — loops still stop once
per iteration), `niko2 format` prints `use "a.niko"` without doubled
quotes, and `use` inside a function is now a checker error (`'use' is
only allowed at the top of a file, not inside a function`) instead of
a silent no-op.

## Niko 2 Alpha 24 — registry hardening

Registries over HTTP(S) now work end to end: point `NIKO_REGISTRY` at an
index URL and `niko2 get <name>` fetches index + tarballs over HTTP
(10-second timeout, plain-English errors for HTTP status / DNS failure /
connection refused / timeout; sha256 verified before the cache is
touched). `niko2 lock` pins the *full* transitive dependency closure
(`{version, source, range}` per package — `range` is the parent package's
requested range), and after every registry `get` the CLI installs any
lockfile-pinned versions missing from the cache, exactly as pinned
(`✓ installed locked dependencies: …`), so a fresh machine reproduces the
author's exact tree. `niko2 get --update <name>` re-resolves and re-pins
the updated package's subtree. Two live requirements with no common
version are a hard, actionable error. `niko2 publish` to a remote
registry is still refused (no auth story yet — publish to a local dir and
sync it to any static file host; a registry is just `index.json` +
`tarballs/`).

```bash
export NIKO_REGISTRY=http://127.0.0.1:8471/index.json
python -m niko2 get greetapp     # over HTTP
python -m niko2 lock .           # pins greetapp AND its transitive deps
```

See `examples/registry-http/` for a worked publish → serve → get → lock →
run demo, and `RELEASE_NOTES_ALPHA24.md` / `ALPHA24_DESIGN.md` for details.

## Niko 2 Alpha 27 — test runner + `assert`

`niko2 test` discovers and runs tests in Niko code itself: `niko2 test
[dir]` recursively finds `*_test.niko` and `test_*.niko` (no config
file). Every top-level `to test_<name>:` with no parameters is a test,
run in total isolation — a fresh VM per test, so one test's globals
can't leak into another. `import`/`use` of the code under test work
exactly like `niko2 run`. A test fails on a raised Niko error or a
failed `assert`; a file that doesn't compile is a reported failure,
never a crash. Output is plain and greppable, with `N passed, M
failed` and exit code 0 iff all pass.

```bash
python -m niko2 test examples/testing
```

```console
PASS test_add (math_test.niko)
PASS test_mul (math_test.niko)
PASS test_add_zero (math_test.niko)
3 passed, 0 failed
```

The new `assert` statement is the idiomatic way to fail a test:

```niko
to test_add:
  assert math.add(20, 22) == 42
  assert math.add(0, 5) == 5, "adding zero should not change the number"
```

The condition must be boolean (the message, if given, must be text —
both typechecked); on failure the error names the expression's source
text and the line: `Line 4: Assertion failed: "math.add(0, 5) == 5" is
not true.` The runner is VM-only; `assert` also works on the WASM
backend (the native backend refuses it with a clean compile error).
No fixtures, mocks, or coverage yet — see `RELEASE_NOTES_ALPHA27.md`.

## Niko 2 Alpha 28 — dogfood sprint: `niko-ssg`

A static site generator written entirely in Niko. `examples/ssg/`
turns the project's own Markdown docs into a small website: `ssg.niko`
reads a page manifest, `markdown.niko` (pure Niko, no imports)
converts each doc with a pragmatic Markdown subset (`#`–`###`,
paragraphs, fenced code, inline code, bold/italic, links, `- ` lists,
`---` → `<hr>`), `pages.niko` builds slugs/nav/index using the
stdlib's `slugify`, and `layout.html` wraps everything in
`{{title}}`/`{{nav}}`/`{{content}}` placeholders.

```bash
cd examples/ssg && ./build.sh   # builds site/ from the repo's docs
python -m niko2 test examples/ssg   # 30 passed, 0 failed
```

Dogfooding found and fixed three language bugs (~30 lines total):
sequential `if`s being parsed as one if/elif chain, forward references
failing inside imported modules, and nested closures reading builtins
past shadowing bindings — each with regression tests. Two findings are
documented limits: `and`/`or` don't short-circuit (both sides always
evaluated), and runtime errors in imported modules report the entry
file's path. See `RELEASE_NOTES_ALPHA28.md` / `ALPHA28_DESIGN.md`.
`site/` is build output (regenerated, not committed).

## Niko 2 Alpha 34 — `niko2 fmt` on the command line

The editor's formatter (LSP `textDocument/formatting`) is now a CLI
command. It runs the exact same pipeline — parse the whole document,
`format_program` — so the terminal and the editor can never disagree.

```bash
niko2 fmt main.niko utils.niko   # rewrite in place, report each file changed
niko2 fmt --check main.niko       # exit 1 if it would reformat, 0 if clean (CI)
cat prog.niko | niko2 fmt         # stdin -> stdout
```

A file that fails to parse is reported with the usual caret diagnostic
and left untouched; a missing file is a plain-English error. CRLF line
endings are normalized to LF (matching what the editor already did).
`niko2 format <file>` still prints one file's formatted source to
stdout. See `RELEASE_NOTES_ALPHA34.md` / `ALPHA34_DESIGN.md`.

## Niko 2 Alpha 35 — `niko2 migrate`, the Niko 1 → Niko 2 migration advisor

Niko 2 is deliberately not Niko 1 (real grammar, typechecker, no Python
fallthrough), so `niko2 migrate` answers *what do I have to change?* A
two-phase analyzer — heuristic scan of 40 Niko 1 idioms plus Niko 2's own
parser + typechecker — reports every divergence with a line number, a
severity, and a pointer into the new `MIGRATION_GUIDE.md` catalog:

```bash
niko2 migrate program.niko              # report: errors, warnings, notes
niko2 migrate --fix program.niko        # apply the safe rewrites in place
niko2 migrate --fix --stdout prog.niko  # fixed source to stdout, report to stderr
```

Exit 1 when errors remain, 0 when only warnings/notes remain, 2 on
usage/IO errors. If Niko 2 accepts the file outright, heuristic errors
are dropped as false positives (a clean check is ground truth). `--fix`
only applies semantics-preserving rewrites (repeat-forever, ask/remove/
random/range call forms, bare `in`, `=`, hex, `//`, `say "a" "b"`,
`set d.k`, `join(l)`, bare `for`, `+=`, `not x is in y`); everything
semantic — `if name:`, `use mylib`, global writes — is reported for a
human to decide. The guide's §8 states the advisor-vs-transpiler
contract. See `RELEASE_NOTES_ALPHA35.md` / `ALPHA35_DESIGN.md`.

## Niko 2 Alpha 36 — `niko2 fuzz`, the differential fuzzer

A seeded, grammar-aware program generator plus a differential runner:
it generates random Niko programs, runs each on the VM, WASM, and
native backends as subprocesses, and compares stdout bytes exactly.
Any disagreement is shrunk (greedy line deletion) and saved to
`fuzz_failures/` with a report. Termination is by construction —
bounded while-loop counters, no `ask` inside loops/functions, small
literals for exponential-time calls — so a hang is always a backend
bug, never a generator artifact.

```bash
niko2 fuzz --seed 1 --cases 1000            # generate + compare (seed always printed)
niko2 fuzz --backend vm,wasm                # subset of backends
niko2 fuzz --native-sample 10               # native only on every 10th case (it's ~3s/compile)
niko2 fuzz --corpus fuzz_failures           # re-run saved cases (regression set)
niko2 fuzz --if-bias --cases 3000           # bias toward otherwise-if chains (Alpha 38)
```

Exit 0 when every case agrees, 1 on divergences, 2 on usage errors.
The final campaign (5,000 cases, VM vs WASM) was fully green; the
bug-finding runs surfaced seven real backend bugs now in
`niko2/KNOWN_LIMITATIONS.md`: the native backend miscompiling
`otherwise if` with a temp-emitting condition (invalid C, plus a silent
wrong-branch variant — **fixed in Alpha 38**), `ask` past stdin EOF
diverges three ways (VM errors, WASM/native yield `""` for text, both
compiled backends hang on `ask number`),
`error_message(try_number(bad-text))` differs VM-vs-WASM,
native skips re-printing the prompt on `ask number` retry, `stop` out of
`repeat`/`for each` leaking the VM's loop iterator (**fixed in Alpha
37**), and comparing `yes`/`no` with numbers diverges across backends.
The fuzzer avoids each open class by construction. See
`RELEASE_NOTES_ALPHA36.md` / `ALPHA36_DESIGN.md`.
