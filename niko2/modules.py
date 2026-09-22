import os
from pathlib import Path
from . import packages as _packages
from .parser import parse, ParseError
from .typecheck import TypeErrorNiko
from .stdlib import module_names
from .diagnostics import Diagnostic
from .ast import (
    Program, ImportStmt, UseStmt, SetStmt, FunctionDef, ReturnStmt,
    RecordExpr, CallExpr, NameExpr, LiteralExpr, AttrExpr,
)


class ImportErrorNiko(Diagnostic):
    """A module-resolution error. `path` is the file the error belongs to,
    so the CLI can render the caret against that file's source."""
    def __init__(self, message, line=None, col=None, path=None):
        super().__init__(message, line=line, col=col)
        self.path = path


# ---------------------------------------------------------------------------
# Alpha 13: `import "path/to/file.niko" as alias` -- multi-file programs.
# Alpha 22: `use "path/to/file.niko"` goes through the same pipeline.
#
# The whole module system is a desugar to a single Program, done once before
# any backend sees the code:
#
#   1. build_module_graph(entry) -- parse every reachable module, resolve
#      paths (relative to the importing file, then cwd), detect cycles.
#      `import` edges make import-units (key mK, qualified access through
#      an alias); `use` edges make use-units (key uK, names merged into
#      scope unqualified). The two key namespaces are disjoint.
#   2. check_units(graph) -- typecheck each module standalone, dependencies
#      first; an `import` binds its alias to `map` in the importing module,
#      a `use` pre-defines the used file's exports as `any`.
#   3. desugar_imports(graph) -- one Program: every non-entry module becomes
#      a wrapper function (`to __import$mK:` / `to __use$uK:`)
#      ... `give back {exports...}`, modules are initialized in dependency
#      order (`set __import$mK$result to __import$mK()`), each
#      `import "…" as a` becomes `set a to __import$mK$result`, and each
#      `use "…"` becomes hoisted `set <name> to __use$uK$result.<name>`
#      bindings before the entry body.
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
    key: str                # unique key: m0, m1, ... (imports) / u0, u1, ... (uses)
    kind: str = 'import'    # 'import' | 'use' | 'entry'
    imports: list = field(default_factory=list)  # [(alias, target Path, line)]
    # [(raw name, target Path or None, line)]; target None = builtin-name
    # use (`use "math"`): no file, the statement is a no-op on every backend.
    uses: list = field(default_factory=list)


def _has_imports(tree):
    return any(isinstance(n, ImportStmt) for n in tree.body)


def _has_uses(tree):
    return any(isinstance(n, UseStmt) for n in tree.body)


def _wrapper_name(key):
    return f'__import${key}'


def _result_name(key):
    return f'__import${key}$result'


def _use_wrapper_name(key):
    return f'__use${key}'


def _use_result_name(key):
    return f'__use${key}$result'


def stdlib_dir():
    """Absolute path of the bundled Niko 2 standard library (niko2/stdlib/)."""
    from . import stdlib as _stdlib_pkg
    return Path(_stdlib_pkg.__file__).parent


def niko_path_roots():
    """Extra module search roots from the NIKO_PATH environment variable
    (os.pathsep-separated). Only existing directories are used."""
    roots = []
    for part in os.environ.get('NIKO_PATH', '').split(os.pathsep):
        part = part.strip()
        if part and Path(part).is_dir():
            roots.append(Path(part))
    return roots


def _resolve_pkg_import(raw, line, importer_path):
    """Resolve ``import "pkg:<name>/path/to/file.niko" as alias``.

    Looks up the pinned version in the nearest enclosing ``niko.lock``
    (via the importing file's directory), else the newest cached version.
    NOTE: offline by construction -- this never touches the network;
    installs happen only in ``packages.install_package`` (``niko2 get``).
    """
    try:
        locked = _packages.locked_package_versions(Path(importer_path).parent)
        return _packages.resolve_pkg_spec(raw, locked)
    except _packages.PackageError as e:
        raise ImportErrorNiko(str(e), line=line, path=str(importer_path))


def resolve_import(raw_path, base_dir, line, importer_path):
    """Resolve an import path to an absolute .niko file.

    Search order (Alpha 14, extended in Alpha 16):
      0. ``pkg:<name>/...`` -- a package from the local package cache
         (``~/.niko/packages``); version from ``niko.lock`` if present,
         else the newest cached version,
      1. the importing file's directory,
      2. each NIKO_PATH directory (if the env var is set),
      3. the bundled standard library -- for paths starting with
         ``stdlib/`` (e.g. ``import "stdlib/text.niko" as text``),
      4. the current working directory.

    A ``stdlib/`` file in the importing file's directory or on NIKO_PATH
    shadows the bundled one. Raises ImportErrorNiko (with the importing
    file's line) when the path is not a .niko file or cannot be found.
    """
    p = (raw_path or '').strip()
    if p.startswith(_packages.PKG_IMPORT_PREFIX):
        return _resolve_pkg_import(p, line, importer_path)
    if Path(p).suffix != '.niko':
        raise ImportErrorNiko(f'import expects a .niko file, got "{raw_path}"',
                              line=line, path=str(importer_path))
    rel = Path(p)
    candidates = [Path(base_dir) / rel]
    candidates += [root / rel for root in niko_path_roots()]
    if rel.parts and rel.parts[0] == 'stdlib' and len(rel.parts) > 1:
        candidates.append(stdlib_dir() / Path(*rel.parts[1:]))
    candidates.append(Path.cwd() / rel)
    for cand in candidates:
        if cand.is_file():
            return cand.resolve()
    raise ImportErrorNiko(f'cannot find module "{raw_path}"',
                          line=line, path=str(importer_path))


def resolve_use(raw_module, base_dir, line, importer_path):
    """Resolve a `use "…"` to an absolute .niko file.

    Returns None for builtin-name uses (`use "math"` etc.): those names
    are builtins on every backend, so the statement is a no-op. The
    builtin check comes before file resolution, matching the old
    VMLoader precedence.

    Lenient like the old loader: tries the raw path, then raw + '.niko',
    through the import search order (importing file's dir -> NIKO_PATH
    dirs -> the bundled stdlib for `stdlib/`-prefixed paths -> cwd). No
    `pkg:` for `use` -- that is `import`'s syntax. Raises ImportErrorNiko
    (pointing at the `use` line) when the file cannot be found.
    """
    raw = (raw_module or '').strip().strip('"\'')
    if module_names(raw):
        return None
    rel = Path(raw)
    variants = [rel] if rel.suffix == '.niko' else [Path(raw + '.niko'), rel]
    bases = [Path(base_dir)] + niko_path_roots()
    candidates = [b / v for b in bases for v in variants]
    if rel.parts and rel.parts[0] == 'stdlib' and len(rel.parts) > 1:
        sub = Path(*rel.parts[1:])
        sub_variants = ([sub] if sub.suffix == '.niko'
                        else [Path(str(sub) + '.niko'), sub])
        candidates += [stdlib_dir() / v for v in sub_variants]
    candidates += [Path.cwd() / v for v in variants]
    for cand in candidates:
        if cand.is_file():
            return cand.resolve()
    raise ImportErrorNiko(f'cannot find module "{raw}"',
                          line=line, path=str(importer_path))


def build_module_graph(entry_path, entry_tree=None, source_overrides=None):
    """Parse every module reachable from entry_path.

    Returns [ModuleUnit] in dependency order (dependencies first, entry
    last). `import` edges make import-units (key mK, qualified access via
    an alias); `use` edges make use-units (key uK, names merged into scope
    unqualified, transitively). A file reached both ways keeps the kind of
    its first visit; the other statement then binds against that unit's
    result record.

    `source_overrides` maps resolved absolute Paths to already-parsed
    Program trees: the LSP passes the in-memory text of open documents
    here so diagnostics reflect unsaved edits instead of the on-disk
    copy. A document that fails to parse is simply left out of the map
    and the on-disk version is used.

    Detects cycles and raises ImportErrorNiko naming the cycle
    (`import cycle: …` / `use cycle: …`); missing/unreadable files and bad
    suffixes raise ImportErrorNiko pointing at the offending line in the
    importing file.
    """
    entry_path = Path(entry_path).resolve()
    units = {}
    order = []
    visiting = []  # [(path, edge_kind)]; edge_kind led *into* the path
    in_progress = set()
    counter = [0]

    def visit(path, importer_path, line, edge):
        if path in in_progress:
            idx = next(i for i, (p, _k) in enumerate(visiting) if p == path)
            cycle_paths = [p for p, _k in visiting[idx:]] + [path]
            cycle_kinds = [k for _p, k in visiting[idx + 1:]] + [edge]
            label = ('use cycle' if all(k == 'use' for k in cycle_kinds)
                     else 'import cycle')
            raise ImportErrorNiko(
                f'{label}: ' + ' -> '.join(p.name for p in cycle_paths),
                line=line, path=str(importer_path))
        if path in units:
            return units[path]
        in_progress.add(path)
        visiting.append((path, edge))
        try:
            if entry_tree is not None and path == entry_path:
                tree = entry_tree
            elif source_overrides and path in source_overrides:
                tree = source_overrides[path]
            else:
                try:
                    src = path.read_text(encoding='utf8')
                except OSError:
                    raise ImportErrorNiko(f'cannot read module "{path.name}"',
                                          line=line, path=str(importer_path))
                try:
                    tree = parse(src)
                except ParseError as e:
                    e.path = str(path)
                    raise
            kind = 'entry' if path == entry_path else edge
            if kind == 'import':
                for n in tree.body:
                    if isinstance(n, UseStmt):
                        raise ImportErrorNiko(
                            "'use' is not supported inside imported modules"
                            " -- use 'import \"...\" as ...' instead",
                            line=n.line, path=str(path))
            elif kind == 'use':
                for n in tree.body:
                    if isinstance(n, ImportStmt):
                        raise ImportErrorNiko(
                            "'import' is not supported inside used modules"
                            " -- import it from the entry file instead",
                            line=n.line, path=str(path))
            key = f'{"u" if kind == "use" else "m"}{counter[0]}'
            counter[0] += 1
            unit = ModuleUnit(path=path, tree=tree, key=key, kind=kind)
            for n in tree.body:
                if isinstance(n, ImportStmt):
                    target = resolve_import(n.path, path.parent, n.line, path)
                    unit.imports.append((n.alias, target, n.line))
                    visit(target, path, n.line, 'import')
                elif isinstance(n, UseStmt):
                    raw = n.module.strip().strip('"\'')
                    target = resolve_use(n.module, path.parent, n.line, path)
                    unit.uses.append((raw, target, n.line))
                    if target is not None:
                        visit(target, path, n.line, 'use')
            units[path] = unit
            order.append(unit)
            return unit
        finally:
            in_progress.discard(path)
            if visiting and visiting[-1][0] == path:
                visiting.pop()

    visit(entry_path, entry_path, None, None)
    return order


def top_exports(tree):
    """Names a module exports: top-level `set` and `to` bindings."""
    names = []
    for n in tree.body:
        if isinstance(n, (SetStmt, FunctionDef)) and n.name not in names:
            names.append(n.name)
    return names


def _unit_exports(graph):
    """{unit.key: [exported names]} for every non-entry unit.

    Import-units export their own top-level `set`/`to` names. Use-units
    export those plus every name they pull in through their own `use`s
    (the old loader flattened transitively-used names into one scope),
    in order, deduplicated.
    """
    by_path = {u.path: u for u in graph}
    memo = {}

    def exports(unit):
        if unit.key in memo:
            return memo[unit.key]
        names = []
        for n in unit.tree.body:
            if isinstance(n, (SetStmt, FunctionDef)) and n.name not in names:
                names.append(n.name)
        if unit.kind == 'use':
            for _raw, target, _line in unit.uses:
                if target is None:
                    continue
                for nm in exports(by_path[target]):
                    if nm not in names:
                        names.append(nm)
        memo[unit.key] = names
        return names

    return {u.key: exports(u) for u in graph[:-1]}


def check_units(graph, entry_names=None):
    """Typecheck every module in dependency order.

    An `import` binds its alias to `map` in the importing module (attribute
    access already types as `any`). A `use` merges names unqualified, so
    each direct use-unit's exports are pre-defined as `any` (builtin-name
    uses need nothing: the checker already knows every builtin). Errors
    inside a module are re-tagged with that module's path so diagnostics
    render against the right source. `entry_names` overrides the entry
    unit's pre-defined names (the REPL passes its accumulated chunk names
    for forward references); by default the entry's own top-level names
    are pre-defined.
    """
    from .typecheck import Checker, MAP, ANY
    by_path = {unit.path: unit for unit in graph}
    exports = _unit_exports(graph)
    for unit in graph:
        c = Checker()
        for alias, _target, line in unit.imports:
            c.define(alias, MAP, line)
        for _raw, target, _line in unit.uses:
            if target is None:
                continue
            c.import_names(exports[by_path[target].key])
        if unit is graph[-1]:
            if entry_names is None:
                entry_names = [n.name for n in unit.tree.body
                               if hasattr(n, 'name')]
            c.import_names(entry_names)
        try:
            c.check(unit.tree)
        except TypeErrorNiko as e:
            e.path = str(unit.path)
            raise


def desugar_imports(graph):
    """Merge the module graph into a single Program (see module docstring).

    Alpha 22: `use` goes through the same pipeline. Every used file
    becomes a `to __use$uK:` wrapper returning a record of its exports
    (its own top-level `set`/`to` names plus names it pulled in through
    its own `use`s -- the old loader flattened those into one scope).
    A nested `use` inside a wrapper becomes unqualified `set` bindings
    against the dependency's already-initialized result record, so each
    used file's top-level code still runs exactly once. In the entry,
    every `use` becomes hoisted `set <name> to __use$uK$result.<name>`
    bindings *before* the entry body: used files run first, later `use`s
    win name conflicts, and the entry's own `set`s always win.
    Builtin-name uses (`use "math"`) are dropped -- those names are
    builtins on every backend, so the statement is a no-op.
    """
    by_path = {unit.path: unit for unit in graph}
    exports = _unit_exports(graph)
    non_entry = graph[:-1]

    def wrapper_name(unit):
        return (_use_wrapper_name(unit.key) if unit.kind == 'use'
                else _wrapper_name(unit.key))

    def result_name(unit):
        return (_use_result_name(unit.key) if unit.kind == 'use'
                else _result_name(unit.key))

    def use_binding(line, nm, tgt):
        return SetStmt(line, nm,
                       AttrExpr(line, NameExpr(line, result_name(tgt)), nm))

    out = []

    # Pre-declare every non-entry unit's result slot before the wrappers:
    # a wrapper body may reference another unit's result global (its own
    # imports / uses), and the WASM backend compiles wrapper bodies when
    # it reaches the `to` statement -- the name must already be in scope
    # at that point. The real initialization calls come after the wrappers
    # (a VM `to` must execute before the name can be called).
    nothing_line = getattr(graph[0].tree, 'line', 1) or 1
    for unit in non_entry:
        out.append(SetStmt(nothing_line, result_name(unit),
                           LiteralExpr(nothing_line, None)))

    for unit in non_entry:
        line = getattr(unit.tree, 'line', 1) or 1
        wname = wrapper_name(unit)
        body = []
        if unit.kind == 'import':
            replacements = iter(unit.imports)
            for s in unit.tree.body:
                if isinstance(s, ImportStmt):
                    alias, target, _l = next(replacements)
                    body.append(SetStmt(s.line, alias, NameExpr(
                        s.line, result_name(by_path[target]))))
                else:
                    body.append(s)
        else:
            replacements = iter(unit.uses)
            for s in unit.tree.body:
                if isinstance(s, UseStmt):
                    _raw, target, _l = next(replacements)
                    if target is None:
                        continue  # builtin-name use: no-op
                    tgt = by_path[target]
                    for nm in exports[tgt.key]:
                        body.append(use_binding(s.line, nm, tgt))
                else:
                    body.append(s)
        rec_items = [(nm, NameExpr(line, nm)) for nm in exports[unit.key]]
        body.append(ReturnStmt(line, RecordExpr(line, rec_items)))
        out.append(FunctionDef(line, wname, [], None, body))
    # Initialize every non-entry unit in dependency order: each wrapper
    # runs exactly once, so a transitively-used file's top-level code runs
    # exactly once too.
    for unit in non_entry:
        out.append(SetStmt(1, result_name(unit),
                           CallExpr(1, NameExpr(1, wrapper_name(unit)), [])))
    entry = graph[-1]
    # Hoisted `use` bindings: every name a used file exports enters the
    # entry scope unqualified, before the entry body.
    use_replacements = iter(entry.uses)
    for s in entry.tree.body:
        if isinstance(s, UseStmt):
            _raw, target, _l = next(use_replacements)
            if target is None:
                continue  # builtin-name use: no-op
            tgt = by_path[target]
            for nm in exports[tgt.key]:
                out.append(use_binding(s.line, nm, tgt))
    import_replacements = iter(entry.imports)
    for s in entry.tree.body:
        if isinstance(s, ImportStmt):
            alias, target, _l = next(import_replacements)
            out.append(SetStmt(s.line, alias, NameExpr(
                s.line, result_name(by_path[target]))))
        elif not isinstance(s, UseStmt):
            out.append(s)
    return Program(getattr(entry.tree, 'line', 1) or 1, out)


def prepare_program(entry_path, entry_tree=None):
    """Build the module graph, typecheck every module, desugar to one Program."""
    graph = build_module_graph(entry_path, entry_tree)
    check_units(graph)
    return desugar_imports(graph)
