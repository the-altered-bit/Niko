# Niko 2.0 — AI Developer Brief

## Mission

Niko is a general-purpose programming language designed to make programming readable without making the language weak.

The project intention is NOT to make another toy language or merely translate English into Python.

The long-term goal is:

> Niko = readable syntax + static safety + strong tooling + its own execution model + a useful standard library + multiple backends.

Niko should be approachable for beginners while remaining capable of real software development.

## Current architecture

Niko 1 is the compatibility/prototype engine. It translates Niko source to Python and also has a browser JavaScript engine.

Niko 2 is the new compiler architecture:

.niko source
  -> Lexer
  -> Parser
  -> AST
  -> Static type checking
  -> Niko IR / bytecode
  -> Niko VM
  -> future native / WebAssembly backends

The Python implementation of the VM is a bootstrap runtime. It is NOT the intended permanent requirement for Niko programs.

## Milestones completed

### Niko 1 foundation
- English-like syntax
- indentation-based blocks
- variables, conditions, loops
- functions
- lists and records
- REPL
- browser IDE
- standard helpers
- friendly errors
- tests and documentation

### Niko 2 Alpha 1
- lexer
- parser
- AST
- compiler-oriented architecture

### Niko 2 Alpha 2
- static type checking
- typed variables
- typed function parameters/returns
- module resolution foundation
- `niko2 check`

### Niko 2 Alpha 3
- Niko IR
- stack bytecode
- Niko VM
- `run`, `build`, `disasm`
- execution no longer requires generating Python source

### Niko 2 Alpha 4
- project manifest: `niko.toml`
- project discovery
- `niko2 init`
- `niko2 info`
- VM-native `.niko` module loading
- shared VM environment for modules
- standard-library organization foundation
- module example and regression testing

## Product intention

Niko should eventually be useful for:
- learning programming
- scripting and automation
- command-line applications
- web services
- data processing
- developer tools
- educational software
- WebAssembly applications
- eventually native applications

Niko is not intended to compete by copying every feature of C++, Rust, Java, Python, or JavaScript. Its differentiation should be coherent language design: simple source syntax with increasingly powerful compiler technology underneath.

## Design principles

1. One canonical syntax for each feature.
2. Readability first, but syntax must be deterministic.
3. Static types should prevent common mistakes without requiring excessive ceremony.
4. Type inference should keep simple code short.
5. Errors should explain what happened and how to fix it.
6. The standard library should be strong before the language surface becomes huge.
7. Niko source should not depend on Python semantics.
8. Backends should be replaceable.
9. New features require tests and documentation.
10. Backward compatibility should be considered, but a clean Niko 2 design has priority over preserving accidental Niko 1 behavior.

## Do not do this

- Do not turn Niko into a collection of hundreds of English aliases.
- Do not make Python syntax the hidden specification.
- Do not implement language semantics with uncontrolled regular-expression rewrites.
- Do not add a feature without deciding its type behavior and error behavior.
- Do not silently change behavior between VM, native, and WASM backends.
- Do not make AI-generated features part of the language without tests and a clear design.

## Recommended next milestones

Alpha 5:
- serialized `.nikoir` loader
- richer diagnostics with source columns/carets
- formatter
- module import/type checking across multiple files
- standard library module API

Alpha 6:
- generic types
- option/result error model
- pattern matching
- package dependency graph
- lock file

Alpha 7:
- language server
- VS Code extension
- debugger protocol

Alpha 8:
- WebAssembly backend

Alpha 9:
- native backend, preferably through a well-defined IR/backend interface

Niko 1 should remain available as a compatibility engine until the Niko 2 conformance suite is strong enough.

## Working rules for another AI

When modifying Niko:
1. Read `NIKO_AI_BRIEF.md`, `NIKO2_DESIGN.md`, and this file.
2. Inspect the current parser/AST/type checker/compiler/VM before changing syntax.
3. Preserve line numbers and useful diagnostics.
4. Add or update tests.
5. Run the complete test suite.
6. Update documentation for user-visible syntax.
7. Never claim a feature is complete unless it is implemented and tested.
8. Prefer small, composable compiler passes over a giant translator.
9. Keep the VM semantics explicit so future native/WASM backends can match them.

## One-sentence explanation for another AI

"Niko is a new general-purpose language that starts with English-readable syntax, but its real ambition is to grow into an independent, typed, multi-backend programming platform rather than remain a Python transpiler."
