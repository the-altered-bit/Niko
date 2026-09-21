from pathlib import Path
from .parser import parse
from .typecheck import check, TypeErrorNiko
from .runtime import Env, execute, NikoRuntimeError
from .compiler import compile_ast
from .vm import VM
from .stdlib import module_symbols, module_names


class ModuleLoader:
    def __init__(self, roots=None): self.roots=[Path(x) for x in (roots or [])]; self.loaded={}
    def resolve(self,name,base):
        raw=name.strip().strip('"\'')
        candidates=[]
        p=Path(raw)
        if p.suffix=='.niko': candidates += [base/p, *[r/p for r in self.roots]]
        else: candidates += [base/(raw+'.niko'), base/raw, *[r/(raw+'.niko') for r in self.roots], *[r/raw for r in self.roots]]
        for c in candidates:
            if c.is_file(): return c.resolve()
        if module_names(raw):
            return None
        raise NikoRuntimeError(f'Cannot find module "{name}".')
    def load_into(self,name,env,base):
        raw=name.strip().strip('"\'')
        if module_names(raw):
            for k,v in module_symbols(raw).items(): env.set(k,v)
            return
        path=self.resolve(name,base)
        if path is None:
            return
        key=str(path)
        if key in self.loaded:
            for k,v in self.loaded[key].data.items():env.set(k,v)
            return
        src=path.read_text(encoding='utf8'); tree=parse(src)
        from .cli import collect_imported_names
        check(tree, collect_imported_names(path.resolve()))
        mod=Env(); self.loaded[key]=mod
        for n in tree.body:
            from .ast import UseStmt
            if isinstance(n,UseStmt): self.load_into(n.module,mod,path.parent)
        execute([n for n in tree.body if n.__class__.__name__!='UseStmt'],mod)
        for k,v in mod.data.items(): env.set(k,v)


class VMLoader:
    """Load .niko modules into one shared VM environment."""
    def __init__(self, roots=None):
        self.roots=[Path(x).resolve() for x in (roots or [])]
        self.loaded={}

    def resolve(self,name,base):
        raw=name.strip().strip('"\'')
        p=Path(raw)
        candidates=[]
        if p.suffix=='.niko':
            candidates += [Path(base)/p, *[r/p for r in self.roots]]
        else:
            candidates += [Path(base)/(raw+'.niko'), Path(base)/raw,
                           *[r/(raw+'.niko') for r in self.roots], *[r/raw for r in self.roots]]
        for c in candidates:
            if c.is_file(): return c.resolve()
        if module_names(raw):
            return None
        raise NikoRuntimeError(f'Cannot find module "{name}".')

    def load(self,name,env,base,vm=None):
        raw=name.strip().strip('"\'')
        if module_names(raw):
            for k,v in module_symbols(raw).items(): env[k]=v
            return
        path=self.resolve(name,base)
        if path is None: return
        key=str(path)
        if key in self.loaded: return
        from .parser import parse
        from .typecheck import check
        from .cli import collect_imported_names
        tree=parse(path.read_text(encoding='utf8'))
        check(tree, collect_imported_names(path.resolve()))
        self.loaded[key]=True
        for n in tree.body:
            if n.__class__.__name__=='UseStmt': self.load(n.module,env,path.parent,vm)
        module=compile_ast(tree)
        (vm or VM()).run_module(module,env)
