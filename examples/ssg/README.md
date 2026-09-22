# niko-ssg

A static site generator written **in the Niko language itself** (Alpha 28
dogfood sprint). It turns the project's Markdown docs into a small static
website: one HTML page per doc, plus an index, wrapped in a single layout
template.

## Layout

| File | What it is |
|---|---|
| `ssg.niko` | The generator. Reads `pages.txt`, converts each doc with `markdown.niko`, applies `layout.html`, writes `site/`. |
| `markdown.niko` | The Markdown-subset → HTML converter (pure Niko, no imports). |
| `pages.niko` | Page helpers: slug, nav, index content (imports `stdlib/text.niko`). |
| `markdown_test.niko`, `pages_test.niko` | Tests, run with `niko2 test`. |
| `layout.html` | Page template with `{{title}}`, `{{nav}}`, `{{content}}` placeholders. |
| `style.css` | Site stylesheet (copied to `site/` by the generator). |
| `build.sh` | Builds the site: writes `pages.txt`, makes `site/`, runs the generator. |
| `site/` | Build output (generated; see git note below). |

## Building

```bash
cd examples/ssg
./build.sh
```

`build.sh` writes `pages.txt` (STDLIB.md, NIKO_AI_BRIEF.md, and every
`RELEASE_NOTES_ALPHA*.md`, paths relative to the repo root), creates
`site/`, and runs `python3 -m niko2 run examples/ssg/ssg.niko` from the
repo root. The generator must run with the repo root as the working
directory because Niko's file builtins resolve relative paths against it.

`niko2 test examples/ssg/` runs the 30 converter/page tests.

## The Markdown subset (exactly what is implemented)

Block level — one pass over lines:

- `# `, `## `, `### ` → `<h1>`–`<h3>`. The `#`s need a trailing space;
  `#nospace` stays a paragraph. Deeper levels (`####`) are not headings.
- Paragraphs: consecutive non-blank lines are joined with a space into
  one `<p>`. A blank line ends the paragraph.
- Fenced code blocks: ```` ``` ```` … ```` ``` ```` → `<pre><code>`,
  with `class="language-x"` when the opening fence names one
  (e.g. ```` ```niko ````). Fence content keeps its indentation and is
  HTML-escaped; Markdown inside fences is not parsed. An unclosed fence
  runs to end of file.
- Unordered lists: consecutive `- ` lines → one `<ul>` of `<li>`s.
  A non-list line closes the list.
- `---` alone on a line → `<hr>`.

Inline level — one left-to-right scan per text chunk:

- `` `code` `` → `<code>` (content escaped, no nested formatting).
- `**bold**` → `<strong>`; `*italic*` → `<em>`. Markers pair with the
  next matching marker; `**` is tried before `*`, and a `*` that is part
  of a `**` pair never closes italics. Unmatched markers are emitted
  literally. Nesting works via recursion (`*a **b** c*`).
- `[label](url)` → `<a href="url">label</a>`. The label may itself
  contain formatting. `[text]` without `(url)` stays literal text.
- Everything else is HTML-escaped (`&`, `<`, `>`; `"` also in URLs).

Page titles come from the first `# ` heading; titles used in `<title>`
and the nav are passed through `plain_title`, which strips `` ` ``,
`**`, `*` markers. Slugs come from the filename via
`stdlib/text.niko`'s `slugify` (`NIKO_AI_BRIEF.md` →
`niko-ai-brief.html`).

## Deliberately not implemented

This is a pragmatic subset, not CommonMark. The following are passed
through as escaped paragraph text, exactly as written:

- tables (`| a | b |`), blockquotes (`>`), ordered lists (`1.`),
  images (`![alt](url)`), setext/lazy constructs, reference links,
  nested lists, inline HTML.

## Niko notes (things the dogfooding surfaced)

- **Niko 2's `and`/`or` do not short-circuit** (unlike Niko 1). Both
  sides always evaluate, so guard idioms like
  `if i < n and item (i + 1) of s is "*":` raise on the bounds check.
  The converter uses nested `if`s instead. See
  `niko2/KNOWN_LIMITATIONS.md` (Alpha 28).
- Import aliases that shadow a builtin name (e.g.
  `import "stdlib/text.niko" as text`) now work inside module
  functions — this was broken before Alpha 28 (fixed in
  `niko2/closures.py`; regression test `tests/test_dogfood.py`).
- Functions in an imported module can call functions defined later in
  the same file, like the entry script always could (fixed in
  `niko2/modules.py`).
- Sequential `if` statements are separate statements again (a parser
  bug briefly made the second one conditional on the first; fixed in
  `niko2/parser.py`).

## Git note

`site/` is generated build output (26 pages, ~40s build). It is
rebuilt by `./build.sh` and does not need to be committed —
`pages.txt` is likewise generated. Only the sources above are meant
to be tracked.
