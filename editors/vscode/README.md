# Niko for VS Code

Syntax highlighting, language-server features, and debugging for the
Niko programming language.

## Features

- **Syntax highlighting** for `.niko` files (TextMate grammar)
- **Language server** (`niko2 lsp`): squiggles for parse and type errors
  as you type, completion for keywords/builtins/your names, hover docs
  and inferred types, go-to-definition, and format-document
- **Debugging** (`niko2 debug`, DAP): line breakpoints, step over / in /
  out, call stack, locals with list/record expansion, and the program's
  `say` output in the Debug Console

## Install

You need Python 3 with the `niko2` package importable (e.g. the
`Niko-v2.0` folder on `PYTHONPATH`).

**From the packaged file** (recommended): in VS Code, run
`Extensions: Install from VSIX…` and pick `niko-0.7.0.vsix`.

**From source**: open this folder, run `npm install` inside `client/`,
then `npx vsce package`, then install the produced `.vsix`.

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

Notes: breakpoints, stepping, and stack traces work across `import`ed
modules (each stack frame opens its own file); `use` imports are not
loaded under the debugger. `ask` for input is answered through a
Niko-specific DAP reverse `input` request — debug clients that don't
answer it (including stock VS Code) give the program `""` after a
30-second timeout.

## Grammar tests

`node test-grammar.js` tokenizes sample lines with the real TextMate
engine and asserts the scopes. Needs one throwaway install first:

```
npm install --no-save vscode-textmate vscode-oniguruma && node test-grammar.js
```
