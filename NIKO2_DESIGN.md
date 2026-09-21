# Niko 2.0 Design

## Architecture

```text
.niko source
   -> Lexer
   -> Parser
   -> AST
   -> Semantic/type analysis
   -> Niko IR
   -> backend(s)
      -> bytecode VM
      -> native
      -> WebAssembly
```

The alpha implementation provides Lexer/Parser/AST/runtime pieces. The Python backend is intentionally not removed yet: it remains the compatibility engine while Niko 2 grows.

## Language principles

- readable first, but syntax must be deterministic
- one canonical spelling for each feature
- optional static types with inference
- errors point to source lines and explain fixes
- standard library before a large language surface
- backwards-compatible migration where practical

## Type syntax

```niko
set age: number to 25
set name: text to "Niko"
set tags: list<text> to ["compiler", "web"]
```

## Planned modules

```text
niko2/
  lexer.py
  parser.py
  ast.py
  checker.py       # next
  ir.py            # next
  compiler.py      # next
  vm.py            # next
  modules.py       # next
  formatter.py     # next
  diagnostics.py   # next
  cli.py
```

## Compatibility

Niko 1 programs continue to use `niko.py`. Niko 2 is additive until the compatibility suite is green.

## Alpha 2 milestone
- Static type checker for variables, functions, control-flow conditions and expressions.
- `niko2 check file.niko` performs validation without execution.
- Module loader resolves `use name` and `use "path.niko"`, caches modules, and exposes module declarations.
- Type errors are reported before runtime execution.

## Alpha 3: Niko IR and Bytecode VM

Alpha 3 introduces a real intermediate representation and stack-based bytecode VM. The compiler lowers the parsed AST directly into Niko instructions; it does not generate Python source. `niko2 run` executes the resulting IR in the VM, while `niko2 build file.niko` writes a `.nikoir` inspection artifact and `niko2 disasm file.niko` prints bytecode.

The current VM is intentionally implemented in Python as a bootstrap runtime. The language architecture is independent of Python and can later receive a native or WASM backend without changing Niko source syntax.
