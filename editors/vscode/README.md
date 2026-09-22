# Niko for VS Code

Syntax highlighting, language-server features, and debugging for the
Niko programming language. Current through Niko 2.0 Alpha 25.

## Features

- **Syntax highlighting** for `.niko` files (TextMate grammar) —
  includes match expressions with guards and list/record patterns,
  `import`/`use`/`pkg:` statements, `ask`, and all builtins
- **Language server** (`niko2 lsp`, stdio): squiggles for parse and type
  errors as you type, completion for keywords/builtins/your names, hover
  docs and inferred types, go-to-definition (follows `import`ed modules
  and `pkg:` packages, even cross-file), format-document, and
  module-aware diagnostics across `import`/`use` graphs
- **Debugging** (`niko2 debug`, DAP): line breakpoints — including
  **conditional** breakpoints — step over / in / out, call stack, locals
  with list/record expansion, the program's `say` output in the Debug
  Console, and **expression evaluation** in the Debug Console / watch
  window. Works across files: `import`ed modules and `use`d modules both
  load under the debugger, breakpoints can target any module, and every
  stack frame opens its own file

## Install

You need Python 3 with the `niko2` package importable (e.g. the
`Niko-v2.0` folder on `PYTHONPATH`).

**From the packaged file** (recommended): in VS Code, run
`Extensions: Install from VSIX…` and pick `niko-0.25.0.vsix`.

**From source**: copy this folder to a scratch dir, run `npm install`
inside `client/`, then `npx vsce package`, then install the produced
`.vsix`.

**Settings**: `niko.pythonPath` — the Python used to launch the language
server and debug adapter (default `python3`).

## Debugging a file

Open a `.niko` file and press F5 (the "Debug current Niko file"
configuration), or create a launch config:

```json
{
  "type": "niko",
  "request": "launch",
  "name": "Debug Niko file",
  "program": "${file}"
}
```

Optional launch attributes: `stopOnEntry` (pause on the first line) and
`inputTimeout` (seconds to wait for `ask` input, default 30).

Notes: breakpoints, stepping, and stack traces work across `import`ed and
`use`d modules (each stack frame opens its own file). `ask` for input is
answered through a Niko-specific DAP reverse `input` request — debug
clients that don't answer it (including stock VS Code) give the program
`""` after the timeout.

## Grammar tests

`node test-grammar.js` tokenizes sample lines with the real TextMate
engine and asserts the scopes; it also verifies the grammar's builtin
list against the checker's canonical `BUILTIN_NAMES`. The two test-only
packages should go in a scratch copy, not this folder:

```
cp -r . /tmp/niko-gram && cd /tmp/niko-gram
npm install --no-save vscode-textmate vscode-oniguruma
NIKO_REPO=/path/to/Niko-v2.0 node test-grammar.js
```
