# Niko 2.0 — Alpha 28: dogfood sprint — `niko-ssg`, a static site generator in Niko

Alpha 28 adds **no new syntax, no new builtins, no network** — it puts
the language to work instead. `niko-ssg` (`examples/ssg/`) is a static
site generator written entirely in Niko that turns the project's own
Markdown docs into a small website. Writing a real, multi-file,
multi-hundred-line program in Niko 2 found **three language bugs**; all
three are fixed, all three have regression tests, and the total diff
is about 30 lines across `niko2/parser.py`, `niko2/modules.py`, and
`niko2/closures.py`.

## What changed

**`examples/ssg/` — the generator** (new):

```console
$ cd examples/ssg && ./build.sh
$ niko2 test examples/ssg/
30 passed, 0 failed
```

- `ssg.niko` — the generator: reads `pages.txt`, converts each doc,
  applies `layout.html`, writes `site/<slug>.html` + `site/index.html`,
  copies `style.css`.
- `markdown.niko` — the Markdown-subset converter. Pure Niko, no
  imports (deliberately self-contained so it's portable).
- `pages.niko` — slug/nav/index helpers, reusing `stdlib/text.niko`'s
  `slugify` (a real-world test of the stdlib + package-less import).
- `markdown_test.niko` (26 tests) + `pages_test.niko` (4 tests),
  written with the Alpha 27 test runner — the first in-repo test
  suite authored in Niko itself.
- `build.sh` — executable; writes `pages.txt` by bash-globbing
  `RELEASE_NOTES_ALPHA*.md`, then runs the generator from the repo
  root (`python3 -m niko2 run examples/ssg/ssg.niko`).
- `layout.html` — single template with `{{title}}`/`{{nav}}`/`{{content}}`
  placeholders; `style.css`; `README.md` with the exact subset spec,
  architecture, and build instructions.

The build renders 26 pages (STDLIB.md, NIKO_AI_BRIEF.md, 24
release-notes files) in ~39s on this machine.

**The Markdown subset** (documented in `examples/ssg/README.md`):

`#`–`###` headings (trailing space required), paragraphs (joined with
spaces), fenced code blocks (``` with optional language →
`<pre><code class="language-x">`; an unclosed fence runs to EOF),
inline code, bold/italic including nesting, `[label](url)` links,
`- ` unordered lists, `---` → `<hr>`. Everything else — tables,
blockquotes, ordered lists — becomes a literal escaped paragraph: a
deliberate pragmatic cut for this sprint, not an oversight.

**Three language bugs, found by dogfooding** (all fixed; regression
tests in new `tests/test_dogfood.py`, 12 cases, VM/WASM/native 3-way
differential; line numbers preserved):

1. **Sequential `if`s were parsed as one if/elif chain**
   (`niko2/parser.py`, `parse_if`). The continuation loop matched any
   later `if ` line, so in `if a: ...` / `if b: ...` the second body
   never ran when the first condition was true — and an `if` block
   ending in a nested `if` swallowed the outer continuation. Now only
   same-indent `otherwise if` / `otherwise:` continue an `if`; a later
   bare `if` always starts a new statement.
2. **Forward references failed inside imported modules**
   (`niko2/modules.py`, `check_units`). Only the entry script
   pre-defined its own top-level names, so a module function couldn't
   call one defined later in the same file. Every unit now pre-defines
   its own top-level names before checking. Mutual recursion across a
   module file is verified at runtime.
3. **Nested closures read builtins past shadowing bindings**
   (`niko2/closures.py`). A nested function reading a builtin-named
   variable bound by an enclosing function — a local `set text to ...`,
   or an `import "stdlib/text.niko" as text` alias inside a module
   wrapper — got the builtin instead of the binding. Capture analysis
   now walks up for an enclosing binding first; only truly unbound
   names skip capture. Alpha 10's pinned rule is unchanged and pinned by
   a test: a builtin name in *direct call position* still means the
   builtin.

## Verification

- `tests/test_dogfood.py` (new, 12 cases): one regression test per
  bug with surrounding behavior coverage (nested `if`s, mutual
  recursion, shadowing in all binding forms), each run against the VM,
  WASM, and native backends.
- `niko2 test examples/ssg/`: 30 passed, 0 failed — the generator's
  own suite, in Niko, through the Alpha 27 runner.
- `examples/ssg/build.sh` on the real repo: 26 pages built, ~39s,
  spot-checked HTML (headings, code fences with language classes,
  bold/italic nesting, links, nav, index).
- Full suite green.

## Limits (future work, not half-built)

Two of these are real language findings that this sprint was too
small to fix; both are in `niko2/KNOWN_LIMITATIONS.md`:

- **`and` / `or` do not short-circuit.** Both operands always
  evaluate, on all three backends — Niko 1 short-circuits, so this is
  a genuine semantic divergence. Guard idioms like
  `if i < n and item (i + 1) of s is "*":` raise an index error; the
  converter uses nested `if`s. Fixing it needs jump-based codegen in
  the VM, WASM, and native backends, plus a decision on what `and` /
  `or` return — a real sprint, not a patch.
- **Runtime errors inside imported modules report the entry file's
  path.** Check-time errors attribute to the right file; a runtime
  failure inside a module says e.g. `Niko error in main.niko` with the
  module's line number. (The DAP adapter already maps frames to files;
  `niko2 run`'s error printer doesn't yet.)

And the pragmatic cuts already documented above: tables, blockquotes,
and ordered lists render as literal escaped paragraphs.

## Build output note

`site/` and `pages.txt` are regenerated by `./build.sh` and are not
committed; `site/` is not yet in `.gitignore` (recommendation
recorded in `ALPHA28_DESIGN.md`). `examples/ssg/site/` exists in this
checkout as build residue — regenerate it, don't review it.
