"""Alpha 29: browser playground adapter.

Single public entry point for the web playground (examples/playground/):

    compile_source_to_wasm(source, filename="main.niko", vdir="/playground")

Takes a Niko source *string* and returns compiled WASM *bytes*. A pure
function: no disk IO, no network, no global state. Programs that use
``import``/``use`` go through the module pipeline; module resolution
uses the real import machinery, so ``import "pkg:..."`` works against
the local package cache and ``import "stdlib/....niko"`` resolves via
the bundled stdlib search path.

Compile failures raise the usual Niko diagnostics, all with
human-readable ``str(e)``:
  - parser.ParseError       (has a .line attribute)
  - typecheck.TypeErrorNiko
  - compiler.CompileError
  - modules.ImportErrorNiko
"""
from pathlib import Path

from .parser import parse
from .typecheck import check
from .modules import _has_imports, _has_uses, prepare_program
from .backends.wasm import WasmBackend


def compile_source_to_wasm(source: str,
                           filename: str = "main.niko",
                           vdir: str = "/playground") -> bytes:
    """Compile a Niko source string to WASM bytes. Pure function.

    ``filename``/``vdir`` only matter for programs with ``import`` or
    ``use``: they fix the virtual path the module graph resolves
    relative imports against.
    """
    tree = parse(source)
    if _has_imports(tree) or _has_uses(tree):
        tree = prepare_program(Path(vdir) / filename, tree)
    else:
        check(tree, [])
    return WasmBackend().compile(tree)
