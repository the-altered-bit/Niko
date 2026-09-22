# Niko 2.0 — Alpha 18 release notes (2026-09-22)

**The debugger follows your imports, and `ask` works while debugging.**
Breakpoints, stepping, and stack traces now work across `import`ed
modules: pause inside a module and the editor opens that module's file at
the right line. The `ask` statement works under the debugger too — the
adapter asks the debug client for input instead of reading the protocol
stream. And the DAP `evaluate` request made the cut: inspect simple
expressions against a paused frame's locals.

```niko
# lib/math.niko
to add with a, b:
    give back a + b

# main.niko
import "lib/math.niko" as m
set r to m.add(2, 3)   # breakpoint inside add: stack shows lib/math.niko
say r
```

## How it works

- **One program, many files.** The debugger now compiles through the
  module pipeline (`niko2/modules.py`): every reachable module is
  typechecked, then the graph is desugared to a single Program whose
  per-module wrapper functions (`__import$mK`, nested defs
  `__import$mK$name`) keep their original line numbers. A frame's
  *qualname* identifies its module by prefix, so each paused frame maps
  back to its own file — breakpoints are per-file, stepping crosses
  files at one-line granularity, and every `stackTrace` frame carries its
  own `source` (file + line).
- **`ask` via a reverse `input` request.** The debuggee's stdin is the
  DAP protocol stream, so the adapter can't use console stdin. Instead,
  when the program executes `ask`, the adapter sends a DAP *reverse*
  request (`input`, carrying the prompt) to the client and waits for its
  answer. The wait is bounded (30s, configurable per-launch via
  `inputTimeout`) and wakes early on disconnect: a client that errors or
  stays silent gets a warning and the program receives `""` — the session
  can never hang on input. This is a Niko-specific reverse request;
  stock VS Code doesn't answer it, so under VS Code `ask` currently
  yields `""` after the timeout (documented in KNOWN_LIMITATIONS).
- **`evaluate` for simple expressions.** The DAP `evaluate` request runs
  an expression against a paused frame's locals: it is typechecked with
  the frame's names defined, then executed on a fresh VM (no trace hook,
  so it can't re-pause the session) with an instruction budget, so a
  runaway expression can't hang anything. Unknown names and parse errors
  come back as clean failures.
- **Cleaner failure mode.** Compile-time problems (parse / check /
  import errors) now end the debug session with a `Niko error: …`
  message and a `terminated` event instead of leaving the client hanging.

## Deliberate limits

- `use` imports are still not loaded under the debugger (a program with
  `use` lines fails with a clear `I don't know what "…"` runtime error).
- The `evaluate` expression runs on a snapshot of the frame's locals;
  side effects it performs (e.g. calling a function that writes a
  global) are real.

Tests: extended `tests/test_dap.py` with a timeout-guarded DAP client —
real sessions over a multi-file fixture (breakpoint inside the imported
module hits with the module file/line in the stack frame, `evaluate`
`a + b` → `5` in the module frame, `stepOut` lands back in the entry
file, the entry-file breakpoint still works), an `ask` program answered
through the reverse `input` request, and a never-answered `input`
request proving the session terminates via the timeout fallback. Full
suite green: 26 Niko 1 cases, 15 Niko 2 cases + nikoir round trip, all
`tests/test_*.py`.
