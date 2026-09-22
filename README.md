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
everything). Ctrl-D leaves; Ctrl-C discards the current block.

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
