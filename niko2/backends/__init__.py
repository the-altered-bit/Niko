"""Niko compiler backends.

Every backend compiles a *checked* Niko AST (the same tree `compile_ast`
consumes) into an executable artifact. This is the IR/backend interface
seed for Alpha 9: the VM backend, the WASM backend, and the future native
backend all speak it.

A backend:
  - `compile(tree) -> bytes`: lower the AST to the target artifact.
  - `output_extension`: e.g. ".wasm".
  - `compile_file(src_path, out_path)`: convenience wrapper used by the CLI.
"""
import abc


class Backend(abc.ABC):
    name = "?"
    description = ""

    @abc.abstractmethod
    def compile(self, tree) -> bytes:
        """Lower a checked AST (niko2.ast.Program) to target bytes."""
        raise NotImplementedError

    @property
    def output_extension(self) -> str:
        return ".out"

    def compile_file(self, src_path, out_path=None):
        from pathlib import Path
        from ..parser import parse
        from ..typecheck import check
        src_path = Path(src_path)
        tree = parse(src_path.read_text(encoding="utf-8"))
        check(tree, [])
        data = self.compile(tree)
        if out_path is None:
            out_path = src_path.with_suffix(self.output_extension)
        Path(out_path).write_bytes(data)
        return Path(out_path)


def get_backend(name):
    """Look up a backend by name (lazy import so backends stay optional)."""
    if name == "wasm":
        from .wasm import WasmBackend
        return WasmBackend()
    raise ValueError(f'unknown backend "{name}"')


BACKENDS = ("wasm",)
