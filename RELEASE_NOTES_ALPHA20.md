# Niko 2.0 — Alpha 20 release notes (2026-09-22)

**Niko 2 gets an interactive REPL.** `niko2 repl` drops you into a live
session on the VM: type Niko 2 code line by line, define functions across
multiple lines, import modules, and see values echoed back — with the
same caret diagnostics as scripts and no way to kill the session with a
bad chunk.

```console
$ python -m niko2 repl
Niko 2 REPL (VM). Type :help for help; :quit or Ctrl-D to leave.
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
niko> import "stdlib/text.niko" as text
niko> say text.capitalize("hi")
Hi
niko> :quit
```

## How it works

- **Prompts:** `niko> ` for a fresh submission, `.... ` while a block is
  open. A line ending in `:` opens a block (a colon inside a string
  doesn't count); finish with a blank line or a dedented line.
  `otherwise:` / `when ` arms keep a match/if open across dedents.
- **Echo:** a bare expression (`1 + 2`) is evaluated and its value
  printed; `nothing` is never echoed. Statements behave as in scripts.
- **Persistence:** every accepted chunk stays in the session — `set x`
  on line 1 is visible to line 50. Each chunk is parsed and typechecked
  against the *whole* accumulated history, and only the new statements
  are compiled and run. One VM and one globals dict live for the whole
  session.
- **Imports:** `import "path/to/file.niko" as alias` goes through the
  normal module pipeline; relative paths resolve against the directory
  where you started the REPL. A module's top-level code runs exactly
  once per session, even if you import it again.
- **Commands:** `:help`, `:quit` / `:exit`, `:reset` (forgets everything).
  Unknown `:foo` gets a plain-English hint. Ctrl-D leaves; Ctrl-C
  discards the current block and re-prompts; errors print the usual
  caret diagnostics and never kill the session.
- **Diagnostics** show cumulative session line numbers, so a caret
  always points at the line you actually typed.

**Deliberate limits:** VM backend only (WASM/native are compile targets,
not REPL targets); no single-chunk undo (`:reset` clears everything);
spaces expected for indentation. See `ALPHA20_DESIGN.md` and
`niko2/KNOWN_LIMITATIONS.md`.

Full suite green: 26 Niko 1 cases, 15 Niko 2 cases + nikoir round trip,
all `tests/test_*.py` (new: `tests/test_repl.py`, 20 checks — scripted
stdin driving real `niko2 repl` subprocesses, hermetic throwaway cwd).
