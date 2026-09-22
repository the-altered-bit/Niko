import argparse
import sys
from pathlib import Path
from .parser import parse,ParseError,format_parse_error
from .diagnostics import format_diagnostic
from .runtime import Env,execute,NikoRuntimeError
from .typecheck import check,TypeErrorNiko
from .modules import ImportErrorNiko
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
    if name!='<memory>':
        from .modules import _has_imports, _has_uses, prepare_program
        if _has_imports(tree) or _has_uses(tree):
            # Alpha 13/22: multi-file program -- resolve, check, and desugar
            # every imported/used module into one Program before any backend
            # sees it.
            return prepare_program(Path(name), tree)
        imported=collect_imported_names(Path(name).resolve())
    else:
        imported=[]
    check(tree, imported); return tree

def _format_error(e, src, name):
    """Render a diagnostic against the file it belongs to.

    Import errors (and parse/type errors inside imported modules) carry
    `.path`; render the caret against that file's source, not the entry's.
    """
    epath=getattr(e, 'path', None) or name
    esrc=src
    if epath!=name:
        try: esrc=Path(epath).read_text(encoding='utf8')
        except OSError: pass
    return format_diagnostic(esrc, e, epath)

def _format_parse_error(e, src, name):
    epath=getattr(e, 'path', None) or name
    esrc=src
    if epath!=name:
        try: esrc=Path(epath).read_text(encoding='utf8')
        except OSError: pass
    return format_parse_error(esrc, e, epath)

def run(src,name='<memory>'):
    try:
        # Alpha 22: `use` goes through the module pipeline like `import`,
        # so the tree is already one desugared program here.
        tree=compile_source(src,name); env=Env()
        execute(tree.body,env)
    except ParseError as e:
        print(_format_parse_error(e, src, name)); return 1
    except (TypeErrorNiko,NikoRuntimeError,ImportErrorNiko) as e:
        print(_format_error(e, src, name)); return 1
    return 0

def main():
    ap=argparse.ArgumentParser(prog='niko2',description='Niko 2 compiler/interpreter')
    ap.add_argument('command',nargs='?',default='run',choices=['run','check','build','disasm','init','info','format','deps','lock','get','publish','lsp','debug','wasm','native','repl'])
    ap.add_argument('file',nargs='?')
    ap.add_argument('--force',action='store_true',help='(get/publish) reinstall the package even if it is already cached/published')
    ap.add_argument('--update',action='store_true',help='(get) re-resolve a registry package to the newest matching version and upgrade the install + lockfile pin')
    ap.add_argument('--registry',help='(publish) registry directory or index URL; default from NIKO_REGISTRY or ~/.niko/config.toml')
    ap.add_argument('-o','--output',help='output file (wasm: <file>.wasm, native: <file>)')
    ap.add_argument('--emit-c',action='store_true',help='(native) also write the generated C source next to the output')
    ap.add_argument('--run',action='store_true',help='run the .wasm with node after building')
    ap.add_argument('--version',action='version',version=f'Niko {VERSION}')
    # Argparse quirk (pre-existing): with two nargs='?' positionals, an
    # option sitting between them breaks parsing, so `niko2 get --update
    # <name>` would die as "unrecognized arguments". Hoist --update out
    # of the argv for the get command before argparse sees it; the
    # trailing form `niko2 get <name> --update` already parses fine.
    argv=sys.argv[1:]
    update_requested=False
    if argv[:1]==['get'] and '--update' in argv:
        argv=[x for x in argv if x!='--update']
        update_requested=True
    a=ap.parse_args(argv)
    a.update=a.update or update_requested
    if a.command=='lsp':
        from .lsp import main as lsp_main; return lsp_main()
    if a.command=='debug':
        from .dap import main as dap_main; return dap_main()
    if a.command=='repl':
        from .repl import main as repl_main; return repl_main()
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
        try:
            lock_path=write_lock_file(root)
        except Exception as e:
            # PackageError (a ValueError) when a pkg:-imported package is
            # not installed: tell the user to run `niko2 get` first.
            print(f'Niko error: {e}'); return 1
        print(f'✓ wrote {lock_path}')
        return 0
    # Alpha 16: install a package into the local cache. Along with
    # `publish` below, the ONLY commands that may touch the network;
    # everything else resolves packages offline from the cache.
    if a.command=='get':
        if not a.file:
            print('Usage: niko2 get <package-directory|git-url> [--force] | niko2 get <name>[@<range>] [--force] | niko2 get --update <name>'); return 2
        from .packages import install_package, PackageError, find_lock_dir, write_package_pin
        if a.update:
            # Alpha 19: re-resolve a registry package to the newest
            # matching version; upgrade the install + the lockfile pin.
            from .registry import update_package
            try:
                upd=update_package(a.file)
            except PackageError as e:
                print(f'Niko error: {e}'); return 1
            if upd.changed:
                print(f'✓ updated {upd.name} {upd.old_version} → {upd.new_version} ({upd.path})')
            else:
                print(f'{upd.name} is already at the newest matching version ({upd.new_version})')
            return 0
        from .registry import (is_registry_spec, split_registry_spec,
                                install_from_registry, resolve_registry)
        if is_registry_spec(a.file):
            # Alpha 19: `niko2 get <name>` / `niko2 get <name>@<range>` --
            # install the newest registry version satisfying the range.
            # The disambiguation rule lives in registry.is_registry_spec;
            # anything else falls through to the Alpha 16 dir/git path.
            try:
                name,range_spec=split_registry_spec(a.file)
                reg=resolve_registry()
                result=install_from_registry(name, range_spec, force=a.force,
                                             registry=reg.spec)
            except PackageError as e:
                print(f'Niko error: {e}'); return 1
            m=result.manifest
            # `get name@range` writes/updates the pin: nearest enclosing
            # niko.lock, else a new one in the current directory.
            try:
                lock_dir=find_lock_dir('.') or Path('.').resolve()
                write_package_pin(lock_dir, m.name, m.version,
                                  f'registry:{reg.spec}', range_spec)
            except PackageError as e:
                print(f'Niko error: {e}'); return 1
            if result.fresh:
                print(f'✓ installed {m.name} {m.version} → {result.path}')
            else:
                print(f'{m.name} {m.version} is already installed ({result.path}) -- use --force to reinstall')
            return 0
        try:
            result=install_package(a.file, force=a.force)
        except PackageError as e:
            print(f'Niko error: {e}'); return 1
        m=result.manifest
        if result.fresh:
            print(f'✓ installed {m.name} {m.version} → {result.path}')
        else:
            print(f'{m.name} {m.version} is already installed ({result.path}) -- use --force to reinstall')
        return 0
    # Alpha 19: publish the package in the current directory to a registry
    # (local directory registries only -- remote needs auth, unsupported).
    if a.command=='publish':
        from .registry import publish_package, PackageError
        try:
            info=publish_package('.', registry=a.registry, force=a.force)
        except PackageError as e:
            print(f'Niko error: {e}'); return 1
        print(f'✓ published {info["name"]} {info["version"]} to {info["spec"]}')
        return 0
    if not a.file:
        print('Usage: niko2 run <file.niko|.nikoir> | niko2 check <file.niko> | niko2 format <file.niko> | niko2 deps [folder] | niko2 lock [folder] | niko2 get <package-directory|git-url> [--force] | niko2 get <name>[@<range>] [--force] | niko2 get --update <name> | niko2 publish [--registry <dir>] [--force] | niko2 init <folder> | niko2 lsp | niko2 debug | niko2 wasm <file.niko> [-o out.wasm] [--run] | niko2 native <file.niko> [-o out] [--run] [--emit-c]'); return 2

    # Alpha 8: compile to WebAssembly.
    if a.command=='wasm':
        import shutil, subprocess
        from .backends.wasm import WasmBackend, CompileError as WasmCompileError
        try: src=Path(a.file).read_text(encoding='utf8')
        except OSError as e: print(f'Niko error: {e}'); return 1
        try:
            tree=compile_source(src,a.file)
            wasm_bytes=WasmBackend().compile(tree)
        except ParseError as e:
            print(_format_parse_error(e, src, a.file)); return 1
        except (TypeErrorNiko,CompileError,NikoRuntimeError,WasmCompileError,ImportErrorNiko) as e:
            print(_format_error(e, src, a.file)); return 1
        out=Path(a.output) if a.output else Path(a.file).with_suffix('.wasm')
        try: out.write_bytes(wasm_bytes)
        except OSError as e: print(f'Niko error: {e}'); return 1
        print(f'✓ built {out} ({len(wasm_bytes)} bytes)', flush=True)
        if a.run:
            node=shutil.which('node')
            if not node:
                print('Niko error: --run needs node.js on PATH'); return 1
            host=Path(__file__).parent/'backends'/'wasm_host.cjs'
            r=subprocess.run([node,str(host),str(out)])
            return r.returncode
        return 0

    # Alpha 9: compile to a native executable via C.
    if a.command=='native':
        import shutil, subprocess
        from .backends.native import NativeBackend, CompileError as NativeCompileError
        try: src=Path(a.file).read_text(encoding='utf8')
        except OSError as e: print(f'Niko error: {e}'); return 1
        try:
            tree=compile_source(src,a.file)
            backend=NativeBackend()
            exe_bytes=backend.compile(tree)
        except ParseError as e:
            print(_format_parse_error(e, src, a.file)); return 1
        except (TypeErrorNiko,CompileError,NikoRuntimeError,NativeCompileError,ImportErrorNiko) as e:
            print(_format_error(e, src, a.file)); return 1
        bext=backend.output_extension
        out=Path(a.output) if a.output else Path(a.file).with_suffix(bext) if bext else Path(a.file).with_suffix('')
        if a.emit_c:
            try: Path(str(out)+'.c').write_text(backend.compile_c(tree),encoding='utf8')
            except OSError as e: print(f'Niko error: {e}'); return 1
        try:
            out.write_bytes(exe_bytes)
            try: out.chmod(0o755)
            except OSError: pass
        except OSError as e: print(f'Niko error: {e}'); return 1
        print(f'\u2713 built {out} ({len(exe_bytes)} bytes)', flush=True)
        if a.run:
            r=subprocess.run([str(out.resolve())])
            return r.returncode
        return 0

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
        # Alpha 22: `use` goes through the module pipeline like `import`
        # (compile_source desugars both); the tree is already one program.
        vm=VM(); env={}
        module=compile_ast(tree)
        vm.run_module(module,env); return 0
    except ParseError as e:
        print(_format_parse_error(e, src, a.file)); return 1
    except (TypeErrorNiko,CompileError,NikoRuntimeError,ImportErrorNiko) as e:
        print(_format_error(e, src, a.file)); return 1
if __name__=='__main__': main()
