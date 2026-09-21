"""Niko Intermediate Representation (IR) and bytecode instruction model."""
from dataclasses import dataclass

@dataclass(frozen=True)
class Instruction:
    op: str
    arg: object = None
    line: int = 0

@dataclass
class FunctionCode:
    name: str
    params: list
    code: list
    return_type: str|None = None
    constants: list = None
    # Alpha 10 closures: qualname is the unique module-wide key
    # ("outer$inner"); captures are names boxed from enclosing functions.
    qualname: str|None = None
    captures: tuple = ()
    nested: bool = False
    def __post_init__(self):
        if self.qualname is None: self.qualname = self.name
        self.captures = tuple(self.captures)

@dataclass
class ModuleCode:
    code: list
    functions: dict
    constants: list

class IRBuilder:
    def __init__(self):
        self.code=[]; self.functions={}; self.constants=[]
    def emit(self,op,arg=None,line=0):
        self.code.append(Instruction(op,arg,line)); return len(self.code)-1
    def patch(self,pos,target): self.code[pos]=Instruction(self.code[pos].op,target,self.code[pos].line)
    def const(self,v,line=0):
        try: return self.constants.index(v)
        except ValueError: self.constants.append(v); return len(self.constants)-1
    def finish(self): return ModuleCode(self.code,self.functions,self.constants)
