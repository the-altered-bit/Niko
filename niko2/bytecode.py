"""Public bytecode compiler/runner API."""
from .compiler import compile_ast, CompileError
from .vm import VM

def compile_source(tree): return compile_ast(tree)
def run_module(module):
    vm=VM(); vm.constants=module.constants; return vm.run_module(module)
