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
