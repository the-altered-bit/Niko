import argparse
from pathlib import Path
from .parser import parse,ParseError,format_parse_error
from .diagnostics import format_diagnostic
from .runtime import Env,execute,NikoRuntimeError
from .typecheck import check,TypeErrorNiko
from .modules import ModuleLoader, VMLoader
from .compiler import compile_ast, CompileError
from .vm import VM
from .project import init_project, find_project_root, read_manifest, collect_project_dependencies, write_lock_file
from .nikoir import save_nikoir, load_nikoir, NikoIRError
from .formatter import format_program
from .stdlib import module_names

VERSION='2.0.0-alpha.5'

def resolve_import_path(name, base):
    raw=name.strip().strip(chr(34)+chr(39)); p=Path(raw)
    candidates=[base/(raw+'.niko'),base/raw] if p.suffix!='.niko' else [base/p]
    for c in candidates:
        if c.is_file(): return c.resolve()
    return None

def collect_imported_names(path_or_name, seen=None):
    imported=[]
    if path_or_name is None: return imported
    if isinstance(path_or_name, str):
        mod=path_or_name.strip().strip(chr(34)+chr(39))
        imported.extend(module_names(mod))
        return imported
    path=Path(path_or_name).resolve()
    seen=set() if seen is None else set(seen)
    if path in seen: return imported
    seen.add(path)
    try:
        tree=parse(path.read_text(encoding='utf8'))
    except OSError:
        return imported
    for n in tree.body:
        if n.__class__.__name__=='UseStmt':
            child=resolve_import_path(n.module, path.parent)
            if child is not None:
                imported += collect_imported_names(child, seen)
            else:
                imported += collect_imported_names(n.module, seen)
        if hasattr(n,'name'):
            imported.append(n.name)
    return imported

def compile_source(src,name='<memory>'):
    tree=parse(src)
    imported=[]
    if name!='<memory>':
        imported=collect_imported_names(Path(name).resolve())
    check(tree, imported); return tree

def run(src,name='<memory>'):
    try:
        tree=compile_source(src,name); env=Env(); loader=ModuleLoader([Path(name).parent if name!='<memory>' else Path('.')])
        for n in tree.body:
            if n.__class__.__name__=='UseStmt': loader.load_into(n.module,env,Path(name).parent if name!='<memory>' else Path('.'))
        execute([n for n in tree.body if n.__class__.__name__!='UseStmt'],env)
    except ParseError as e:
        print(format_parse_error(src, e, name)); return 1
    except (TypeErrorNiko,NikoRuntimeError) as e:
        print(format_diagnostic(src, e, name)); return 1
    return 0

def main():
    ap=argparse.ArgumentParser(prog='niko2',description='Niko 2 compiler/interpreter')
    ap.add_argument('command',nargs='?',default='run',choices=['run','check','build','disasm','init','info','format','deps','lock','lsp','debug'])
    ap.add_argument('file',nargs='?')
    ap.add_argument('--version',action='version',version=f'Niko {VERSION}')
    a=ap.parse_args()
    if a.command=='lsp':
        from .lsp import main as lsp_main; return lsp_main()
    if a.command=='debug':
        from .dap import main as dap_main; return dap_main()
    if a.command=='init':
        target=a.file or '.'
        root=init_project(target); print(f'✓ initialized Niko project: {root}'); return 0
    if a.command=='info':
        root=find_project_root(a.file or '.')
        print(f'Niko {VERSION}\nProject: {root}')
        try:
            m=read_manifest(root)
            print(f"Name: {m.get('name','niko_app')}\nVersion: {m.get('version','0.1.0')}\nEntry: {m.get('entry','main.niko')}")
        except ValueError as e: print(f'Niko error: {e}'); return 1
        return 0
    if a.command=='format':
        if not a.file:
            print('Usage: niko2 format <file.niko>'); return 2
        try:
            src=Path(a.file).read_text(encoding='utf8')
            tree=parse(src)
            print(format_program(tree), end='')
            return 0
        except ParseError as e:
            print(format_parse_error(src, e, a.file)); return 1
        except OSError as e:
            print(f'Niko error in {a.file}: {e}'); return 1
    if a.command=='deps':
        root=find_project_root(a.file or '.')
        graph=collect_project_dependencies(root)
        if not graph:
            print('No project dependencies found.')
            return 0
        for rel, deps in sorted(graph.items()):
            if deps:
                print(f'{rel}: {", ".join(deps)}')
            else:
                print(f'{rel}: (none)')
        return 0
    if a.command=='lock':
        root=find_project_root(a.file or '.')
        lock_path=write_lock_file(root)
        print(f'✓ wrote {lock_path}')
        return 0
    if not a.file:
        print('Usage: niko2 run <file.niko|.nikoir> | niko2 check <file.niko> | niko2 format <file.niko> | niko2 deps [folder] | niko2 lock [folder] | niko2 init <folder> | niko2 lsp | niko2 debug'); return 2

    # Alpha 5: a .nikoir file is a pre-compiled artifact. It skips lexing,
    # parsing and type checking entirely -- `run` and `disasm` load the
    # serialized IR straight into the VM. `check` and `build` don't apply
    # to an already-built artifact, since there is no source left to check
    # or compile.
    if Path(a.file).suffix=='.nikoir':
        if a.command not in ('run','disasm'):
            print(f"Niko error: 'niko2 {a.command}' needs a .niko source file, not a .nikoir artifact."); return 2
        try: module=load_nikoir(a.file)
        except NikoIRError as e: print(f'Niko error in {a.file}: {e}'); return 1
        if a.command=='disasm':
            for i,x in enumerate(module.code): print(f'{i:04} {x.op:16} {x.arg!r}')
            return 0
        try:
            vm=VM(); vm.run_module(module,{}); return 0
        except NikoRuntimeError as e:
            print(f'Niko error in {a.file}: {e}'); return 1

    try: src=Path(a.file).read_text(encoding='utf8')
    except OSError as e: print(f'Niko error: {e}'); return 1
    try:
        tree=compile_source(src,a.file)
        if a.command=='check': print(f'✓ {a.file}: no type errors'); return 0
        if a.command in ('build','disasm'):
            module=compile_ast(tree)
            if a.command=='build':
                out=Path(a.file).with_suffix('.nikoir')
                save_nikoir(module,out); print(f'✓ built {out}'); return 0
            for i,x in enumerate(module.code): print(f'{i:04} {x.op:16} {x.arg!r}')
            return 0
        # Alpha 4: execute modules and the main program in one shared VM environment.
        vm=VM(); env={}
        base=Path(a.file).parent.resolve()
        loader=VMLoader([find_project_root(a.file)])
        for n in tree.body:
            if n.__class__.__name__=='UseStmt': loader.load(n.module,env,base,vm)
        from .ast import Program
        main_tree=Program(tree.line,[n for n in tree.body if n.__class__.__name__!='UseStmt'])
        module=compile_ast(main_tree)
        vm.run_module(module,env); return 0
    except ParseError as e:
        print(format_parse_error(src, e, a.file)); return 1
    except (TypeErrorNiko,CompileError,NikoRuntimeError) as e:
        print(format_diagnostic(src, e, a.file)); return 1
if __name__=='__main__': main()
