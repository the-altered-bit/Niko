"""Alpha 29: tests for the browser-playground adapter niko2/playground.py.

Skips the node end-to-end case if node.js is not on PATH (mirrors
tests/test_wasm.py, which does the same).
"""
import pathlib
import shutil
import subprocess
import sys
import tempfile

import pytest

root = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

from niko2.playground import compile_source_to_wasm
from niko2.parser import ParseError
from niko2.compiler import CompileError

node = shutil.which("node")
host = root / "niko2" / "backends" / "wasm_host.cjs"

FIB = (
    "to fib with n:\n"
    "    if n is smaller than 2:\n"
    "        give back n\n"
    "    give back fib(n - 1) + fib(n - 2)\n"
    "say fib(10)\n"
)


def test_compile_returns_wasm_bytes():
    wasm = compile_source_to_wasm(FIB)
    assert isinstance(wasm, bytes)
    assert wasm[:4] == b"\x00asm", "missing WASM magic"
    assert len(wasm) > 100


def test_stdlib_import_resolves_from_repo():
    src = 'import "stdlib/text.niko" as text\nsay text.upper("hello")\n'
    wasm = compile_source_to_wasm(src)
    assert wasm[:4] == b"\x00asm"


def test_parse_error_carries_line():
    with pytest.raises(ParseError) as excinfo:
        compile_source_to_wasm("set x to 1\nif x:\nsay wrong-indent\n")
    assert excinfo.value.line is not None
    assert excinfo.value.line >= 1
    assert str(excinfo.value), "ParseError must be human-readable"


def test_file_builtin_raises_clean_compile_error():
    with pytest.raises(CompileError) as excinfo:
        compile_source_to_wasm('say try_read_file("notes.txt")\n')
    msg = str(excinfo.value)
    assert "try_read_file" in msg, f"message should name the builtin: {msg!r}"
    assert "Traceback" not in msg


def test_ask_program_references_niko_input():
    wasm = compile_source_to_wasm('ask "name? " into name\nsay "hi " + name\n')
    assert b"niko_input" in wasm, "ask must import the niko_input host function"


@pytest.mark.skipif(node is None, reason="node.js not on PATH")
def test_end_to_end_run_with_node_host():
    src = "say 2 + 3 * 4\nsay upper(\"abc\")\n"
    wasm = compile_source_to_wasm(src)
    with tempfile.TemporaryDirectory() as tmp:
        p = pathlib.Path(tmp) / "prog.wasm"
        p.write_bytes(wasm)
        r = subprocess.run([node, str(host), str(p)],
                           capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"node failed: {r.stderr[:300]}"
    assert r.stdout == "14\nABC\n", f"unexpected stdout: {r.stdout!r}"


def test_pure_function_deterministic():
    assert compile_source_to_wasm(FIB) == compile_source_to_wasm(FIB)
    a = compile_source_to_wasm(FIB, filename="other.niko")
    b = compile_source_to_wasm(FIB, filename="other.niko")
    assert a == b
