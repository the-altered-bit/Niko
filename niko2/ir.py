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
        # Alpha 30: dedupe only across exact-type matches. The old
        # constants.index(v) used Python ==, so True==1, False==0 and
        # 1==1.0 collided; with operand-returning and/or (Alpha 30) that
        # silently returned the wrong constant (e.g. `say yes and 1`
        # printing `yes` because the 1 literal reused the True slot).
        for i,c in enumerate(self.constants):
            if type(c) is type(v) and c==v: return i
        self.constants.append(v); return len(self.constants)-1
    def finish(self): return ModuleCode(self.code,self.functions,self.constants)
