# Alpha 28 design: dogfood sprint — `niko-ssg`

## Problem

Niko 2 had a compiler, three backends, a package manager with a
registry, an LSP, a debugger, a REPL, and a test runner — but nobody
had ever written a real program in it. The repo's own Niko code was
test fixtures and examples measured in tens of lines. A language that
has never survived a few hundred lines of real use is a language whose
bugs are all still hiding.

The classic fix is dogfooding: build something genuinely useful in the
language itself. A static site generator for the project's own docs
was the right size — string-heavy, file-heavy, multi-module, and with
a success criterion you can check by reading the output.

## Design

### Why a Markdown converter

A converter is a parser, a text transformer, and a test author all at
once: it exercises string manipulation, records, lists, recursion,
nested control flow, and file I/O in a single program, and wrong
output is immediately visible in the generated HTML. It also forced
the project to use the Alpha 27 test runner for real — `markdown.niko`
was developed test-first, with `markdown_test.niko` running under
`niko2 test examples/ssg/`.

### Architecture (`examples/ssg/`)

```
build.sh ──► pages.txt ──► ssg.niko ──► site/*.html
                (manifest)   ├─ markdown.niko  (converter, pure Niko, no imports)
                             ├─ pages.niko     (slug/nav/index, imports stdlib/text.niko)
                             └─ layout.html    (template)
```

- **`ssg.niko`** is the orchestrator: read the manifest, convert each
  doc, substitute into the template, write pages + index, copy the
  stylesheet. It exercises file builtins (`read_file`, `write_file`)
  and the module pipeline on every run.
- **`markdown.niko`** is deliberately import-free — one self-contained
  module, a single `import "markdown.niko"` away from reuse in any
  Niko project. Block level is one pass over lines (state machine:
  paragraph accumulation, fenced code, list grouping); inline level is
  one left-to-right scan per text chunk (code spans, then bold/italic
  via recursive nesting, then links).
- **`pages.niko`** imports `stdlib/text.niko` for `slugify` — the
  first real use of the stdlib from user code, and the import that
  exposed bug #3 below.
- **`build.sh`** is bash, not Niko: it writes the manifest (globbing
  `RELEASE_NOTES_ALPHA*.md`) and runs the generator from the repo root
  (Niko's file builtins resolve against the working directory). The
  bash layer is orchestration only; all rendering logic is Niko.
- **Template mechanism**: `layout.html` carries three plain
  placeholders — `{{title}}`, `{{nav}}`, `{{content}}` — filled by
  string replacement in `ssg.niko`. No template language, no
  templating module. Rejected: building the template engine in Niko
  too — it would have made the sprint about templating instead of
  dogfooding, and placeholder substitution is exactly sufficient for a
  docs site.

### The subset: a deliberate cut

The subset (`#`–`###`, paragraphs, fenced code with optional language,
inline code, bold/italic incl. nesting, links, `- ` lists, `---` →
`<hr>`) covers everything the project's release-notes Markdown
actually uses. Tables, blockquotes, and ordered lists exist in the
docs but are rare, and each would have doubled the converter's parser
surface for little dogfood value — they render as literal escaped
paragraphs, documented in `examples/ssg/README.md`. The subset spec
is exact enough that a later sprint can close it feature by feature.

### The three bugs (each ~10 lines of fix)

**1. `parse_if` swallowed sequential `if`s** (`niko2/parser.py`).
The continuation loop accepted any later line starting with `if `,
so `if a: ...` followed by `if b: ...` parsed as one if/elif chain —
the second body never ran when the first condition was true. Worse,
an `if` block *ending* in a nested `if` swallowed whatever followed
it. The fix: only a same-indent `otherwise if` / `otherwise:`
continues the chain; a bare `if` always starts a fresh statement. The
sweep after the fix: no parser behavior changed for well-formed
if/elif/else, and the standing caution in NEXT_STEPS about
`parse_stmt`'s flat `startswith` chain applied — view surrounding
lines, re-run the suite.

**2. Forward references failed inside imported modules**
(`niko2/modules.py`, `check_units`). Pre-defining top-level names
before checking only happened for the *entry* unit, so a module
function calling a later-defined function in the same file was an
"unknown name" error. The fix: every unit pre-defines its own
top-level names first. Runtime verification includes mutual recursion
across a module file — self-recursion worked before only by accident
of ordering.

**3. Nested closures received builtins past shadowing bindings**
(`niko2/closures.py`). Capture analysis asked "is this name a
builtin?" before "is it bound by an enclosing function?" — so a
nested function reading a builtin-*named* name bound by an enclosing
function (a local `set text to ...`, or an `import ... as text` alias
inside a module wrapper) captured the builtin. The real program
broke here: `pages.niko` does `import "stdlib/text.niko" as text`,
and its helper functions saw the *builtin* `text` module-less call
set instead of the alias. Fix: walk up for an enclosing binding
first; only truly unbound names skip capture. Alpha 10's pinned rule
is preserved and pinned by a test: a builtin name in *direct call
position* still means the builtin (`text.slugify(...)` calls the
alias, `length(...)` still calls the builtin).

### `site/` and `pages.txt`: gitignore, don't commit

The build output is regenerated by `./build.sh` from the docs, which
are already committed. Committing it would duplicate every release
notes file into a second tracked location that goes stale the moment
the source changes. `site/` and `pages.txt` are build residue; the
design note recommends `site/` go in `.gitignore` (it currently isn't
— flagged, not fixed, so the sprint stays scoped to its own changes).

## Deliberately not built

- **Short-circuit `and`/`or`** — a real semantic divergence from
  Niko 1 (which short-circuits) and the one finding that actively
  shaped the converter (nested `if`s instead of guard idioms). It
  needs jump-based codegen across three backends plus a decision on
  what `and`/`or` *return*; that's a language-design sprint, and this
  one had three bugs to fix already. In `KNOWN_LIMITATIONS.md`.
- **Per-file runtime error paths** — check-time errors attribute
  correctly (Alpha 13), the DAP adapter maps frames to files (Alpha
  18), but `niko2 run`'s error printer still reports the entry file.
  In `KNOWN_LIMITATIONS.md`.
- **Subset extensions** (tables, blockquotes, ordered lists, `####`+,
  reference links) — specified as not-implemented in the SSG README,
  each a small future bite.

## As built

- `examples/ssg/` builds the 26-page docs site in ~39s from a clean
  `./build.sh`; spot-checked output (headings, fenced code with
  `language-x` classes, nested bold/italic, links, nav, index).
- The generator's own suite — `niko2 test examples/ssg/` — is 30
  passed, 0 failed, the first test suite in the repo written in Niko
  itself (via the Alpha 27 runner).
- All three language fixes are ~30 lines total across
  `niko2/parser.py`, `niko2/modules.py`, `niko2/closures.py`, with 12
  regression tests in `tests/test_dogfood.py` running VM/WASM/native
  3-way differential. Line numbers preserved in all fixes.
- No new Niko syntax, no new builtins, no network.
- Gotcha captured for future authors: `and`/`or` evaluate both sides —
  write bounds checks as nested `if`s until short-circuiting lands.
