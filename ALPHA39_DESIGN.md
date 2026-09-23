# ALPHA39_DESIGN — The Beginner's Guide

## Goal
A beautiful, child-friendly, A-to-Z beginner's guide for Niko 2.0:
`docs/niko-beginners-guide.html`. Casper's brief: "beginner friendly documents
about a-z in simple english that can be read and understand even childrens,
use graphics codes terminal in beautiful."

## Audience
A 10-year-old (or a grown-up beginner) with zero programming experience.
Simple English: short sentences, no jargon without an immediate plain-words
explanation. Warm, encouraging, picture-book tone.

## Relationship to the existing guide
`docs/niko-guide.html` (127 KB) is the deeper reference: full A–Z reference,
4-week plan, exercises, style guide, glossary. The beginner's guide is the
gentle on-ramp and links to it as "the deeper guide". No duplication of the
reference material.

## Structure (17 chapters)
1. What is Niko + setup (VS Code extension `niko-lang.niko`, `niko2` CLI, playground)
2. First program (`say "hello"`)
3. Variables (`set`)
4. `ask` (text + `ask number`)
5. Decisions (`if` / `otherwise if` / `otherwise`)
6. Loops (`repeat`, `for each`, `while`, `stop`, `skip`)
7. Functions (`to ... with ...`, `give back`)
8. Lists
9. Records
10. Text tricks (stdlib text)
11. Numbers and math
12. Errors (reading error messages)
13. `use` modules
14. Standard library tour
15. Three backends (`run`, `wasm --run`, `native --run`)
16. Mini projects (guessing game, todo list, quiz)
17. Where to go next (`niko2 test`, `niko2 get`, `niko2 fmt`, project ideas)

## Presentation
- Single self-contained HTML file: inline CSS, inline SVG, one tiny inline
  `<script>` for Niko syntax highlighting. No external dependencies —
  works offline from a `file://` open.
- Every concept: tiny explanation + code block + terminal window showing the
  exact command typed and the exact output produced.
- Graphics: inline SVG only — a reusable mascot ("Niko" face, defined once in
  `<defs>` and referenced with `<use>`), plus simple diagrams (variable boxes,
  if/otherwise flowchart, loop circle, function machine, ask conversation,
  record bundle).
- Navigation: table of contents with anchor links; "Try it yourself" challenge
  boxes per chapter; tip boxes with the mascot.
- Color scheme: warm cream paper, coral/teal/sunny-yellow accents, dark
  editor-style code blocks, macOS-style terminal windows.

## Example verification method (accuracy is non-negotiable)
- Every `.niko` example written as a real file in `~/workspace/a39_examples/`
  (outside the repo), run with the real CLI
  (`python3 -m niko2 run <file>` from the repo root).
- `ask` examples: stdin piped with `printf`, exact piped input recorded.
- The error-message example: exact stdout/stderr/exit code captured.
- The backends example: one program run via `run`, `wasm --run`, `native --run`;
  all three outputs captured (node + cc available on the build machine).
- Verified examples also kept in the repo as `docs/beginners-examples/*.niko`
  so readers can download and run them.
- Terminal sessions in the guide show the literal command + literal output.
  Nothing is invented, tidied, or paraphrased.

## As built
- `docs/niko-beginners-guide.html` (~60 KB, single file): 17 chapters, 28 code
  blocks, 27 terminal sessions, inline SVG mascot (`#niko-face` in `<defs>`,
  reused via `<use>`) + 6 diagrams (variable boxes, if/otherwise flowchart,
  loop circle, function machine, ask conversation, record bundle), TOC with
  anchor links, "Try it yourself" boxes, tip boxes, tiny inline JS Niko syntax
  highlighter. Zero external URLs — verified self-contained, works from
  `file://`. HTML validated well-formed (tag balance check).
- **24 example files, every one verified against the real CLI**
  (`docs/beginners-examples/*.niko` + `use_demo/` pair): 22 written and
  byte-verified by a dedicated worker (exact stdout/stderr/exit code captured
  from `python3 -m niko2 run`), plus 2 written and verified by the coordinator
  (`decisions_chain.niko`, `text_tricks2.niko`). All 24 re-run green from
  their repo locations. Terminal sessions in the guide show the literal
  command + literal output. Two readability normalizations (documented, not
  hidden): machine-specific absolute paths shortened to basenames in the
  `oops.niko` error line and the wasm/native `✓ built` lines — a reader's
  paths will differ anyway.
- Example-verification method: examples live in `~/workspace/a39_examples/`
  (outside the repo) during writing; `ask` examples piped via `printf` with
  the exact input recorded; backends example run all three ways
  (`run`, `wasm <file> --run`, `native <file> --run` — note the arg order).
- **Language surprises found while writing examples** (reported, not fixed —
  out of scope for this sprint):
  1. Niko runtime errors go to **stdout**, not stderr (exit code 1).
  2. No `is even` / `is odd` condition in the language; parity via `n % 2 is 0`.
  3. Lists cannot grow: no `push`/`append`; `set item N of list to X` replaces.
  4. `niko2 fmt` normalizes `"..."` to `'...'`.
  5. Booleans print as `yes`/`no`.
  6. wasm/native build lines (`✓ built <path> (<N> bytes)`) print to stdout
     before program output.
  7. `ask` prompt and piped stdin share one line in captured output.
- Full suite green; Niko 1's 26-case suite untouched and green.
