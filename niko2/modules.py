from pathlib import Path
from .parser import parse, ParseError
from .typecheck import check, TypeErrorNiko
from .runtime import Env, execute, NikoRuntimeError
from .compiler import compile_ast
from .vm import VM
from .stdlib import module_symbols, module_names
from .diagnostics import Diagnostic
from .ast import (
    Program, ImportStmt, UseStmt, SetStmt, FunctionDef, ReturnStmt,
    RecordExpr, CallExpr, NameExpr, LiteralExpr,
)


class ImportErrorNiko(Diagnostic):
    """A module-resolution error. `path` is the file the error belongs to,
    so the CLI can render the caret against that file's source."""
    def __init__(self, message, line=None, col=None, path=None):
        super().__init__(message, line=line, col=col)
        self.path = path


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


# ---------------------------------------------------------------------------
# Alpha 13: `import "path/to/file.niko" as alias` -- multi-file programs.
#
# The whole module system is a desugar to a single Program, done once before
# any backend sees the code:
#
#   1. build_module_graph(entry) -- parse every reachable module, resolve
#      paths (relative to the importing file, then cwd), detect cycles.
#   2. check_units(graph) -- typecheck each module standalone, dependencies
#      first; an `import` binds its alias to `map` in the importing module.
#   3. desugar_imports(graph) -- one Program: every non-entry module becomes
#      a wrapper function `to __import$mK:` ... `give back {exports...}`,
#      modules are initialized in dependency order
#      (`set __import$mK$result to __import$mK()`), and each
#      `import "…" as a` becomes `set a to __import$mK$result`.
#
# The wrapper-function shape is deliberate: Alpha 10's closure machinery
# (lexical scoping, capture-by-reference, boxed forward references, sibling
# recursion) makes module locals work with no renaming pass, and running each
# wrapper exactly once is the import cache. `$` is not a legal Niko
# identifier character, so synthetic names can never collide with user code.

from dataclasses import dataclass, field


@dataclass
class ModuleUnit:
    path: object            # resolved absolute Path of the module file
    tree: object            # parsed Program
    key: str                # unique key: m0, m1, ...
    imports: list = field(default_factory=list)  # [(alias, target Path, line)]


def _has_imports(tree):
    return any(isinstance(n, ImportStmt) for n in tree.body)


def _wrapper_name(key):
    return f'__import${key}'


def _result_name(key):
    return f'__import${key}$result'


def resolve_import(raw_path, base_dir, line, importer_path):
    """Resolve an import path to an absolute .niko file.

    Relative to the importing file's directory first, then the cwd.
    Raises ImportErrorNiko (with the importing file's line) when the path
    is not a .niko file or cannot be found.
    """
    p = (raw_path or '').strip()
    if Path(p).suffix != '.niko':
        raise ImportErrorNiko(f'import expects a .niko file, got "{raw_path}"',
                              line=line, path=str(importer_path))
    for cand in (Path(base_dir) / p, Path.cwd() / p):
        if cand.is_file():
            return cand.resolve()
    raise ImportErrorNiko(f'cannot find module "{raw_path}"',
                          line=line, path=str(importer_path))


def build_module_graph(entry_path, entry_tree=None):
    """Parse every module reachable from entry_path.

    Returns [ModuleUnit] in dependency order (dependencies first, entry
    last). Detects import cycles and raises ImportErrorNiko naming the
    cycle; missing/unreadable files and bad suffixes raise ImportErrorNiko
    pointing at the `import` line in the importing file.
    """
    entry_path = Path(entry_path).resolve()
    units = {}
    order = []
    visiting = []
    in_progress = set()
    counter = [0]

    def visit(path, importer_path, import_line):
        if path in in_progress:
            cycle = visiting[visiting.index(path):] + [path]
            raise ImportErrorNiko(
                'import cycle: ' + ' -> '.join(p.name for p in cycle),
                line=import_line, path=str(importer_path))
        if path in units:
            return units[path]
        in_progress.add(path)
        visiting.append(path)
        try:
            if entry_tree is not None and path == entry_path:
                tree = entry_tree
            else:
                try:
                    src = path.read_text(encoding='utf8')
                except OSError:
                    raise ImportErrorNiko(f'cannot read module "{path.name}"',
                                          line=import_line, path=str(importer_path))
                try:
                    tree = parse(src)
                except ParseError as e:
                    e.path = str(path)
                    raise
            if path != entry_path:
                for n in tree.body:
                    if isinstance(n, UseStmt):
                        raise ImportErrorNiko(
                            "'use' is not supported inside imported modules"
                            " -- use 'import \"...\" as ...' instead",
                            line=n.line, path=str(path))
            key = f'm{counter[0]}'
            counter[0] += 1
            unit = ModuleUnit(path=path, tree=tree, key=key)
            for n in tree.body:
                if isinstance(n, ImportStmt):
                    target = resolve_import(n.path, path.parent, n.line, path)
                    unit.imports.append((n.alias, target, n.line))
                    visit(target, path, n.line)
            units[path] = unit
            order.append(unit)
            return unit
        finally:
            in_progress.discard(path)
            if visiting and visiting[-1] == path:
                visiting.pop()

    visit(entry_path, entry_path, None)
    return order


def top_exports(tree):
    """Names a module exports: top-level `set` and `to` bindings."""
    names = []
    for n in tree.body:
        if isinstance(n, (SetStmt, FunctionDef)) and n.name not in names:
            names.append(n.name)
    return names


def check_units(graph):
    """Typecheck every module in dependency order.

    An `import` binds its alias to `map` in the importing module (attribute
    access already types as `any`). Errors inside a module are re-tagged
    with that module's path so diagnostics render against the right source.
    The entry unit keeps the legacy `use` name collection.
    """
    from .typecheck import Checker, MAP, ANY
    for unit in graph:
        c = Checker()
        for alias, _target, line in unit.imports:
            c.define(alias, MAP, line)
        if unit is graph[-1]:
            from .cli import collect_imported_names
            c.import_names(collect_imported_names(unit.path))
        try:
            c.check(unit.tree)
        except TypeErrorNiko as e:
            e.path = str(unit.path)
            raise


def desugar_imports(graph):
    """Merge the module graph into a single Program (see module docstring)."""
    key_of = {unit.path: unit.key for unit in graph}
    out = []

    # Pre-declare every module's result slot before the wrappers: a wrapper
    # body may reference another module's result global (its own imports),
    # and the WASM backend compiles wrapper bodies when it reaches the `to`
    # statement -- the name must already be in scope at that point. The real
    # initialization calls come after the wrappers (a VM `to` must execute
    # before the name can be called).
    nothing_line = getattr(graph[0].tree, 'line', 1) or 1
    for unit in graph[:-1]:
        out.append(SetStmt(nothing_line, _result_name(unit.key),
                           LiteralExpr(nothing_line, None)))

    def import_replacement(unit):
        # iterator over (alias, target key) in source order
        for alias, target, _line in unit.imports:
            yield alias, _result_name(key_of[target])

    for unit in graph[:-1]:
        line = getattr(unit.tree, 'line', 1) or 1
        wname = _wrapper_name(unit.key)
        replacements = import_replacement(unit)
        body = []
        for s in unit.tree.body:
            if isinstance(s, ImportStmt):
                alias, rname = next(replacements)
                body.append(SetStmt(s.line, alias, NameExpr(s.line, rname)))
            else:
                body.append(s)
        rec_items = [(nm, NameExpr(line, nm)) for nm in top_exports(unit.tree)]
        body.append(ReturnStmt(line, RecordExpr(line, rec_items)))
        out.append(FunctionDef(line, wname, [], None, body))
    for unit in graph[:-1]:
        out.append(SetStmt(1, _result_name(unit.key),
                           CallExpr(1, NameExpr(1, _wrapper_name(unit.key)), [])))
    entry = graph[-1]
    replacements = import_replacement(entry)
    for s in entry.tree.body:
        if isinstance(s, ImportStmt):
            alias, rname = next(replacements)
            out.append(SetStmt(s.line, alias, NameExpr(s.line, rname)))
        else:
            out.append(s)
    return Program(getattr(entry.tree, 'line', 1) or 1, out)


def prepare_program(entry_path, entry_tree=None):
    """Build the import graph, typecheck every module, desugar to one Program."""
    graph = build_module_graph(entry_path, entry_tree)
    check_units(graph)
    return desugar_imports(graph)
