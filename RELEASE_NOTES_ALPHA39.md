# Release Notes — Alpha 39: The Beginner's Guide

**One-liner:** Niko now has a picture-book on-ramp — a beautiful,
child-friendly A-to-Z tutorial that a 10-year-old can read.

## What shipped

**`docs/niko-beginners-guide.html`** — a single, self-contained page
(inline CSS, inline SVG, one tiny inline highlighter script; zero external
dependencies, works offline from a `file://` open) that teaches Niko 2.0
from zero:

1. What is Niko + setup (VS Code extension `niko-lang.niko`, the `niko2`
   CLI, the browser playground)
2. First program (`say "hello"`)
3. Variables (`set`)
4. `ask` (text and `ask number`)
5. Decisions (`if` / `otherwise if` / `otherwise`)
6. Loops (`repeat`, `for each`, `while`, `stop`, `skip`)
7. Functions (`to ... with ...`, `give back`)
8. Lists, 9. Records, 10. Text tricks (stdlib), 11. Numbers and math
12. Reading error messages, 13. `use` modules, 14. Standard library tour
15. Three backends, one language (`run` / `wasm --run` / `native --run`)
16. Three mini projects (guessing game, todo list, quiz show)
17. Where to go next (`niko2 test`, `niko2 get`, `niko2 fmt`, project ideas)

Every concept gets a tiny explanation, a code block, and a terminal window
showing the exact command and output. A friendly SVG mascot pops up with
hints; inline SVG diagrams show how `if` branches, how loops circle, what
a variable/function/record looks like. "Try it yourself" challenge boxes
in every chapter.

**`docs/beginners-examples/`** — all 24 example programs as runnable
`.niko` files (including the `use_demo/` two-file pair), so readers can
download and run every snippet in the book.

## Accuracy

Every example was run against the real `niko2` CLI and the guide shows
the true output — nothing invented. The deeper `docs/niko-guide.html`
remains the full reference; this guide links to it as "the deeper guide"
instead of duplicating it.

## Notes

- While writing examples, several language quirks were confirmed and
  documented in `ALPHA39_DESIGN.md` ("As built"): errors print to stdout
  (not stderr), lists can't grow, `fmt` normalizes quotes, booleans print
  as `yes`/`no`, wasm/native print a `✓ built` line first. None were
  changed — they're guide material, and candidate future-sprint items.
- Full test suite green; Niko 1's 26-case suite untouched.

See `ALPHA39_DESIGN.md`.
