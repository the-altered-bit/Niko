# Release notes — Alpha 34: `niko2 fmt` on the command line

The formatter that has lived inside the editor (LSP
`textDocument/formatting`) is now a CLI command. `niko2 fmt` formats
files in place, checks formatting in CI, and pipes through stdin:

```bash
niko2 fmt main.niko utils.niko      # rewrite in place, report each file changed
niko2 fmt --check main.niko         # exit 1 if it would reformat, 0 if clean
cat prog.niko | niko2 fmt           # stdin -> stdout
```

This is a formatter-*exposure* sprint, not a formatter redesign: no
formatting rule changed. The formatter was already factored into
`niko2/formatter.py` (`format_program`) with the LSP handler as a thin
wrapper, so the CLI runs the exact same pipeline — parse the whole
document, `format_program`, emit. The CLI and the editor cannot
disagree by construction, and `tests/test_fmt.py` proves it on every
parseable `.niko` file in the repo.

## What changed

- **`niko2 fmt [files...] [--check]`** (new, in `niko2/cli.py`):
  `fmt_source_text` (one source string → formatted or a plain-English
  parse error) and `cmd_fmt` (the driver). Multiple files are formatted
  in place; each changed file is reported as `formatted <file>`.
  `--check` never writes: it prints `would reformat <file>` and exits 1
  if anything would change, 0 if everything is already formatted. With
  no file arguments it reads stdin and writes stdout (the classic pipe);
  `niko2 fmt --check < prog.niko` is silent and exits 0/1.
- **Error paths**: a file that fails to parse is reported with the
  usual caret diagnostic naming the file, exits non-zero, and is left
  byte-untouched. A missing file is a plain-English `Niko error`,
  also non-zero.
- **`niko2 format` is unchanged**: it still prints one file's formatted
  source to stdout. `fmt` is the in-place / multi-file / check-mode
  sibling.
- **CRLF decision**: `fmt` normalizes CRLF line endings to LF —
  exactly what the LSP handler already did (the parser accepts `\r\n`,
  `format_program` emits `\n`), so both paths agree. Documented in
  `ALPHA34_DESIGN.md`.

## Tests

- New `tests/test_fmt.py` (pytest, 248 cases): the CLI/LSP differential
  (`lsp_format(src) == format_program(parse(src))` and
  `fmt_source_text(src) == format_program(parse(src))`) over all 79
  parseable `.niko` files under `tests/`, `examples/`,
  `niko2/stdlib/`; idempotency (`fmt(fmt(x)) == fmt(x)`) over the same
  corpus; and CLI behavior — in-place multi-file rewrite with per-file
  reporting, already-formatted byte-identical silence, `--check` exit
  codes with files untouched, stdin piping, parse-error and
  missing-file paths, CRLF→LF normalization, empty file.
- Full suite green: pytest, 26 Niko 1 cases, 16 Niko 2 cases +
  nikoir round trip.

## Known limitations

None new. Formatting style itself is untouched — any style gripe is a
future formatter-redesign sprint, not this one.
