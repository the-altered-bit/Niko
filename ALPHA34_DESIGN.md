# Alpha 34 — `niko2 fmt`: design

Exposure sprint: bring the editor formatter to the command line.
Explicitly **not** a formatter-redesign sprint — zero formatting rules
change. The only acceptable output delta versus the LSP handler is none.

## Starting state

The task brief assumed the formatting logic might still live inside
`niko2/lsp.py` and need factoring. It didn't: the formatter already
lives in `niko2/formatter.py` as `format_program(tree) -> str`, and the
LSP `textDocument/formatting` handler (`Server._format`) is already a
thin wrapper:

```python
tree = parse(text)
new_text = format_program(tree)
```

No refactor was needed and none was done — factoring already-factored
code would only add risk. The work was: (1) prove the equivalence with
a differential test, (2) add the CLI on the same pipeline.

## CLI design

`niko2 fmt [files...] [--check]`, implemented as two functions in
`niko2/cli.py` next to the other command drivers:

- `fmt_source_text(src, name)` — one source string in, `(True,
  formatted)` or `(False, plain-English error)` out. This is the whole
  pipeline: `parse` + `format_program`. Both the file/stdin paths and
  (conceptually) the LSP handler run it, so the three can never
  disagree.
- `cmd_fmt(files, check_only)` — the driver, returns an exit code:
  - **No files**: read stdin, write formatted source to stdout.
    `--check` on stdin is silent, exit 0/1.
  - **Files**: read each, format, rewrite in place when different,
    printing `formatted <file>` per changed file. `--check` prints
    `would reformat <file>` per file that would change and exits 1
    without writing anything.
  - **Parse error**: the caret diagnostic naming the file, non-zero
    exit, file left untouched. Missing/unreadable file: plain-English
    `Niko error`, non-zero exit.

Argparse note: the shared `file` positional is `nargs='?'` (one file),
which cannot express `fmt`'s multi-file form, and changing it would
touch every command. Following the existing `--update` hoist precedent,
`main()` splits `fmt`'s file list out of `argv` before `parse_args`
(non-flag args after `fmt`); flags like `--check` stay for argparse.
`niko2 format <file>` (stdout, single file) is kept unchanged for
backwards compatibility — `fmt` is its in-place/multi-file/check-mode
sibling, not a replacement.

## Decisions

- **CRLF → LF.** `format_program` always emits `\n`; the parser
  accepts `\r\n`. So the LSP handler already normalized CRLF to LF on
  every format. `fmt` does the same — the alternative (preserving CRLF)
  would have made the CLI *disagree* with the editor, violating the
  sprint's acceptance bar. Documented, tested
  (`test_fmt_normalizes_crlf`).
- **Empty file → `"\n"`.** `format_program` of an empty program is
  `"\n"`, which is what the LSP handler already produced. Kept.
- **`--check` exit codes**: 0 = everything already formatted, 1 =
  something would change (or an error occurred). CI-shaped.
- **Quiet when clean**: like `gofmt`, `fmt` prints only what changed.
  An already-formatted file is byte-identical afterwards (tested).

## As built

Exactly as designed. `tests/test_fmt.py`: 248 cases — the LSP/shared
differential, the CLI-core/shared differential, and idempotency, each
over all 79 parseable `.niko` files under `tests/`, `examples/`,
`niko2/stdlib/` (the one unparseable file,
`tests/cases/err_otherwise.niko`, is a Niko 1 error fixture and is
skipped), plus 11 CLI behavior tests (in-place multi-file, `--check`
semantics, stdin pipe, parse-error/missing-file paths with files
untouched, CRLF normalization, empty file). Full suite green: pytest,
26 Niko 1 cases, 16 Niko 2 cases + nikoir round trip.
