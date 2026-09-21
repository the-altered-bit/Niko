# Niko 2.0 Alpha

This release starts the Niko 2 architecture without breaking Niko 1.

## Run the new engine

```bash
python -m niko2 examples/hello_v2.niko
```

Expected output:

```text
Hello from Niko
Niko is growing.
Niko is growing.
Niko is growing.
The answer is 42
```

## Run tests

```bash
python -m pytest -q
```

## What changed

Niko 1 remains in `niko.py`. Niko 2 introduces a real lexer, Pratt expression parser, AST, typed declarations, function objects, a structured runtime, and a dedicated CLI.

## Next implementation milestones

- static semantic/type checker
- unified diagnostics with source spans and suggestions
- module/package loader
- formatter
- Niko IR
- bytecode VM
- debugger
- WebAssembly backend
- native backend
- language server
