#!/usr/bin/env python3
"""Alpha 34: `niko2 fmt` -- the editor formatter on the command line.

Acceptance bars:
1. CLI and LSP can never disagree: both run parse + format_program on the
   whole document (the LSP textDocument/formatting handler is a thin wrapper
   around niko2.formatter.format_program). test_lsp_matches_shared_formatter
   proves this on every parseable .niko file in the repo.
2. Idempotency: fmt(fmt(x)) == fmt(x) on the whole corpus.
3. CLI behavior: in-place multi-file formatting, --check exit codes, stdin
   piping, and the error paths (unparseable / missing file) which must never
   clobber the input.

The corpus is every .niko file under tests/, examples/, and niko2/stdlib/
that parses. (tests/cases/err_otherwise.niko is a Niko 1 error fixture and
does not parse under Niko 2 -- it is skipped, not a failure.)
"""
import io
import os
import pathlib
import subprocess
import sys

import pytest

root = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

from niko2.parser import parse, ParseError
from niko2.formatter import format_program
from niko2 import lsp as lsp_mod
from niko2.cli import fmt_source_text


def _corpus():
    files = []
    for d in ("tests", "examples", "niko2/stdlib"):
        files.extend(sorted((root / d).rglob("*.niko")))
    out = []
    for f in files:
        try:
            src = f.read_text(encoding="utf8")
            parse(src)
        except ParseError:
            continue
        out.append(pytest.param(f, src, id=str(f.relative_to(root))))
    return out


CORPUS = _corpus()
assert len(CORPUS) > 50, "corpus unexpectedly small"


def lsp_format(text):
    """Format exactly the way the LSP textDocument/formatting handler does:
    parse the whole document, run format_program, return the new text."""
    srv = lsp_mod.Server(io.BytesIO(), io.BytesIO())
    uri = "file:///fmt-differential.niko"
    srv.docs[uri] = text
    edits = srv._format({"textDocument": {"uri": uri}})
    assert edits, "LSP _format returned no edits"
    assert len(edits) == 1
    return edits[0]["newText"]


@pytest.mark.parametrize("path,src", CORPUS)
def test_lsp_matches_shared_formatter(path, src):
    """The LSP handler and the shared formatter produce byte-identical
    output for every corpus file -- the acceptance bar for the refactor."""
    assert lsp_format(src) == format_program(parse(src))


@pytest.mark.parametrize("path,src", CORPUS)
def test_cli_core_matches_shared_formatter(path, src):
    """The fmt command's core is the same pipeline, not a second copy."""
    ok, out = fmt_source_text(src, str(path))
    assert ok, out
    assert out == format_program(parse(src))


@pytest.mark.parametrize("path,src", CORPUS)
def test_idempotent(path, src):
    """fmt(fmt(x)) == fmt(x): formatting is a fixed point."""
    once = format_program(parse(src))
    twice = format_program(parse(once))
    assert twice == once, f"not idempotent: {path}"


def _niko2(*args, input=None):
    e = dict(os.environ)
    e["PYTHONPATH"] = str(root) + os.pathsep + e.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "niko2", *args],
        capture_output=True, text=True, cwd=str(root), env=e, input=input,
    )


def test_fmt_in_place_multiple_files(tmp_path):
    a = tmp_path / "a.niko"
    b = tmp_path / "b.niko"
    clean = tmp_path / "clean.niko"
    a.write_text('if yes:\n        say "hi"\n', encoding="utf8")
    b.write_text('say "x"\n', encoding="utf8")
    clean.write_text("say 'x'\n", encoding="utf8")
    res = _niko2("fmt", str(a), str(b), str(clean))
    assert res.returncode == 0, res.stderr or res.stdout
    # both dirty files rewritten, each reported
    assert "formatted" in res.stdout and str(a) in res.stdout
    assert str(b) in res.stdout
    # the already-formatted file is byte-identical and not reported
    assert clean.read_text(encoding="utf8") == "say 'x'\n"
    assert str(clean) not in res.stdout
    # formatted output parses and is a fixed point
    for p in (a, b):
        text = p.read_text(encoding="utf8")
        assert format_program(parse(text)) == text


def test_fmt_already_formatted_is_byte_identical(tmp_path):
    p = tmp_path / "f.niko"
    src = "set x to 1\nsay x\n"
    assert format_program(parse(src)) == src  # test's own premise
    p.write_text(src, encoding="utf8")
    res = _niko2("fmt", str(p))
    assert res.returncode == 0
    assert res.stdout == ""
    assert p.read_bytes() == src.encode("utf8")


def test_fmt_check_reports_and_does_not_write(tmp_path):
    dirty = tmp_path / "dirty.niko"
    clean = tmp_path / "clean.niko"
    dirty.write_text('if yes:\n        say "hi"\n', encoding="utf8")
    clean.write_text("say 'x'\n", encoding="utf8")
    before = dirty.read_bytes()
    res = _niko2("fmt", "--check", str(dirty), str(clean))
    assert res.returncode == 1, res.stdout
    assert "would reformat" in res.stdout and str(dirty) in res.stdout
    assert str(clean) not in res.stdout
    assert dirty.read_bytes() == before, "check mode must not write"


def test_fmt_check_clean(tmp_path):
    p = tmp_path / "c.niko"
    p.write_text("say 'x'\n", encoding="utf8")
    res = _niko2("fmt", "--check", str(p))
    assert res.returncode == 0
    assert res.stdout == ""


def test_fmt_stdin_to_stdout():
    res = _niko2("fmt", input='if yes:\n        say "hi"\n')
    assert res.returncode == 0, res.stderr
    assert res.stdout == 'if yes:\n    say \'hi\'\n'


def test_fmt_stdin_check():
    assert _niko2("fmt", "--check", input="say 'x'\n").returncode == 0
    assert _niko2("fmt", "--check", input='if yes:\n        say "hi"\n').returncode == 1


def test_fmt_parse_error_leaves_file_untouched(tmp_path):
    p = tmp_path / "bad.niko"
    src = "set x to =\n"
    p.write_text(src, encoding="utf8")
    res = _niko2("fmt", str(p))
    assert res.returncode == 1
    assert "bad.niko" in res.stdout, res.stdout  # names the file
    assert p.read_text(encoding="utf8") == src, "broken file must not be clobbered"


def test_fmt_stdin_parse_error():
    res = _niko2("fmt", input="set x to =\n")
    assert res.returncode == 1
    assert res.stderr.strip() != ""


def test_fmt_missing_file(tmp_path):
    res = _niko2("fmt", str(tmp_path / "nope.niko"))
    assert res.returncode == 1
    assert "nope.niko" in res.stdout


def test_fmt_normalizes_crlf(tmp_path):
    p = tmp_path / "crlf.niko"
    p.write_bytes(b'say "hi"\r\nsay "yo"\r\n')
    res = _niko2("fmt", str(p))
    assert res.returncode == 0, res.stderr or res.stdout
    # Documented decision (ALPHA34_DESIGN.md): fmt normalizes CRLF to LF,
    # exactly as the LSP handler does (parse accepts \r\n, format emits \n).
    assert p.read_bytes() == b"say 'hi'\nsay 'yo'\n"


def test_fmt_empty_file(tmp_path):
    p = tmp_path / "empty.niko"
    p.write_bytes(b"")
    res = _niko2("fmt", str(p))
    assert res.returncode == 0
    # format_program([]) == "\n" -- same bytes the LSP handler produces.
    assert p.read_bytes() == b"\n"
    assert lsp_format("") == "\n"
