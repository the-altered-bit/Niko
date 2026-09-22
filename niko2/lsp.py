"""Niko 2 language server (LSP over stdio).

Start it with `niko2 lsp`. It speaks enough of the Language Server
Protocol for a good editing experience in any LSP-capable editor:

- `textDocument/publishDiagnostics` — parse errors and type errors, with
  the line/column positions from `niko2/diagnostics.py`. Alpha 23: when
  the open file has top-level `import`/`use` statements it is checked
  through the module pipeline (`build_module_graph` + `check_units`),
  so import aliases and `use`d names resolve exactly as at compile time
  (no phantom unknown-name errors); an `alias.attr` naming something the
  module doesn't export gets its own diagnostic; an unresolvable import
  or use yields exactly one diagnostic on its line; errors inside module
  files are published against the right file/line. Diagnostics are
  read-only: they never write files and never touch the network.
  Alpha 26: the module path caches each module's parsed tree keyed on
  the file's (mtime, size), so unchanged modules are not re-parsed on
  every keystroke; a changed module invalidates its own entry and
  everything downstream (its importers, transitively); unsaved open
  documents still override disk and always count as changed.
- `textDocument/completion` — keywords, builtins, and names defined in
  the file. Alpha 26: inside an `import "pkg:…"` string, installed
  package names from the local package cache (before the `/`), then
  `.niko` files inside the named package (after the `/`); plain import
  strings are untouched, and the network is never consulted.
- `textDocument/hover` — builtin docs and inferred types of your names;
  follows `import` across files (Alpha 21: `alias.name` shows the
  target's signature and doc comment from the module file) and `use`
  across files (Alpha 22: a bare name merged in by `use` shows the
  target's signature and doc comment from the used file)
- `textDocument/definition` — jump to where a name was defined, following
  `import` across files (Alpha 17: `alias.name` jumps to the top-level
  `set`/`to` in the module file; the import's path string jumps to the
  module file itself) and `use` across files (Alpha 22: a bare name
  merged in by `use` jumps to the top-level `set`/`to` in the used file;
  the use's path string jumps to the used file itself)
- `textDocument/formatting` — runs `niko2 format` on the whole document
- `textDocument/prepareRename` + `textDocument/rename` (Alpha 32) —
  scope-aware rename refactoring: locals, parameters, loop variables
  and top-level `set`/`to` (with cross-file `alias.name` propagation to
  importing files, and bare-name propagation to `use`ing files);
  import aliases rename in-file only; builtins, keywords, string
  contents, import path strings and record fields are never renameable

Hover and go-to-definition are line-oriented: Niko 2 AST nodes carry line
numbers but not columns, so results resolve to the nearest sensible line
(see `niko2/symbols.py`). Rename is column-precise instead: it re-scans
each involved line's text (with strings and comments masked out) to
find the exact character ranges, while scope resolution still reuses
`symbols.find_symbol`, so shadowing behaves exactly like hover and
go-to-definition. Cross-file definition reuses
`niko2/modules.py`'s import/use resolution, so `pkg:` imports, relative
`./` imports inside packages, NIKO_PATH, and the bundled stdlib all work
exactly as they do at compile time; an import or use that can't be
resolved simply yields no definition (never an error).
"""
import os
import re
import sys
import traceback
from pathlib import Path
from urllib.parse import urlparse, unquote
from urllib.request import url2pathname

from .jsonrpc import read_message, write_message
from .parser import parse, ParseError
from .typecheck import check, TypeErrorNiko, BUILTIN_NAMES
from .formatter import format_program
from .symbols import collect_symbols, find_symbol, all_names, _infer
from .diagnostics import Diagnostic
from .ast import Node, ImportStmt, UseStmt, AttrExpr, NameExpr, SetStmt, FunctionDef
from . import packages as _packages
from .modules import (resolve_import, resolve_use, ImportErrorNiko,
                      build_module_graph, check_units, stdlib_dir,
                      _unit_exports as _module_unit_exports)

VERSION = '2.0.0-alpha.7'

KEYWORDS = [
    'set', 'say', 'if', 'otherwise', 'match', 'when', 'to', 'give back',
    'for each', 'while', 'repeat', 'stop', 'skip', 'ask', 'use', 'import',
    'as',
    'and', 'or', 'not', 'is', 'is not', 'in', 'yes', 'no', 'nothing',
]

BUILTIN_DOCS = {
    'text': 'text(x) — turn a value into text.',
    'number': 'number(t) — turn text into a number, or raise a friendly error.',
    'length': 'length of X — how many items or characters.',
    'item_of': 'item_of(N, X) — the Nth item (positions start at 1).',
    'upper': 'upper(t) — TEXT in upper case.', 'lower': 'lower(t) — text in lower case.',
    'trim': 'trim(t) — remove surrounding whitespace.',
    'replace': 'replace(t, a, b) — replace every a with b.',
    'split': 'split(t, sep) — split text into a list.',
    'join': 'join(list, sep) — join a list into text.',
    'sorted': 'sorted(l) — a sorted copy.', 'reversed': 'reversed(l) — a reversed copy.',
    'unique': 'unique(l) — remove duplicates, keep order.',
    'sum': 'sum(l) — add the numbers.', 'average': 'average(l) — the mean.',
    'max': 'max(l) — the biggest.', 'min': 'min(l) — the smallest.',
    'abs': 'abs(x)', 'ceil': 'ceil(x)', 'floor': 'floor(x)',
    'round': 'round(x, digits)', 'sqrt': 'sqrt(x)', 'pi': 'pi — 3.14159…',
    'random_int': 'random_int(a, b) — a whole number from a to b.',
    'has': 'has(c, x) — yes if x is in c.',
    'today': 'today() — the date, like "2026-09-21".',
    'now': 'now() — the current date and time.',
    'sleep': 'sleep(seconds) — pause.',
    'keys': 'keys(record) — the record\'s labels.',
    'starts_with': 'starts_with(t, p) — yes if t starts with p.',
    'ends_with': 'ends_with(t, p) — yes if t ends with p.',
    'count_of': 'count_of(t, part) — how many times part appears.',
    'pick': 'pick(l) — a random item from the list.',
    'write_file': 'write_file(name, text) — write text to a file.',
    'append_file': 'append_file(name, text) — add text to the end of a file.',
    'read_file': 'read_file(name) — read a whole file as text.',
    'read_lines': 'read_lines(name) — read a file as a list of lines.',
    'file_exists': 'file_exists(name) — yes if the file exists.',
    'ok': 'ok(v) — a result holding the value v.',
    'error': 'error(msg) — a result holding an error message.',
    'is_ok': 'is_ok(r) — yes if r is an ok result.',
    'is_error': 'is_error(r) — yes if r is an error result.',
    'unwrap': 'unwrap(r) — the value inside, or fail with the error message.',
    'unwrap_or': 'unwrap_or(r, default) — the value inside, or default.',
    'error_message': 'error_message(r) — the message of an error result.',
    'try_read_file': 'try_read_file(name) — ok(text), or error(...) if the file is missing.',
    'try_number': 'try_number(t) — ok(number), or error(...) if the text is not a number.',
    'niko_range': 'numbers A to B — a list from A to B (internal helper).',
}

WORD = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')

# Matches the text before the cursor when it sits inside an import
# string that starts with `pkg:`, e.g. `import "pkg:hel` or
# `import 'pkg:hello/sub/ut`. Group 2 is everything after `pkg:`.
# The trailing `[^'"]*` keeps the match inside the string: once the
# closing quote is typed the cursor is no longer "inside" it.
_PKG_IMPORT_RE = re.compile(r'\bimport\s+([\'"])pkg:([^\'"]*)$')


def _cached_package_names():
    """Sorted [(name, newest version)] installed in the local package
    cache (`$NIKO_PKG_CACHE` or `~/.niko/packages`).

    Offline by construction -- this only lists directories. An empty or
    unreadable cache yields [], never an error.
    """
    try:
        root = _packages.default_cache_root()
        if not root.is_dir():
            return []
        names = set()
        for child in root.iterdir():
            if not child.is_dir():
                continue
            m = re.match(r'^(.*)-(\d+\.\d+\.\d+)$', child.name)
            if m:
                names.add(m.group(1))
        out = []
        for name in sorted(names):
            vers = _packages.installed_versions(name, root)
            out.append((name, vers[-1][1] if vers else None))
        return out
    except OSError:
        return []


def _pkg_dir_for_completion(uri, name):
    """Absolute Path of the cached package `name` for completion, or
    None when it can't be resolved.

    Mirrors compile-time resolution: the nearest enclosing `niko.lock`
    pin wins, else the newest cached version (Alpha 24's known limit
    applies -- `pkg:` imports *inside* cached packages resolve to the
    newest cached version, and so does this). Offline: never touches the
    network; anything unresolvable (unknown package, corrupt lockfile,
    missing pin) yields None.
    """
    try:
        locked = {}
        entry = _uri_to_path(uri)
        if entry is not None:
            try:
                locked = _packages.locked_package_versions(
                    Path(entry).parent)
            except _packages.PackageError:
                locked = {}
        return _packages.resolve_package(name, locked.get(name))
    except _packages.PackageError:
        return None


def _word_at(line_text, character):
    for m in WORD.finditer(line_text):
        if m.start() <= character <= m.end():
            return m.group(0)
    return None


def _uri_to_path(uri):
    """Filesystem path for a `file:` URI, or None for anything else."""
    try:
        parts = urlparse(uri)
    except Exception:
        return None
    if parts.scheme != 'file':
        return None
    path = url2pathname(unquote(parts.path))
    return path or None


def _path_to_uri(path):
    return Path(path).as_uri()


def _loc(uri, line0):
    return {'uri': uri, 'range': {
        'start': {'line': line0, 'character': 0},
        'end': {'line': line0, 'character': 0}}}


def _iter_nodes(root):
    """Yield every AST node under `root` (generic dataclass walk)."""
    seen = set()

    def visit(x):
        if isinstance(x, Node):
            if id(x) in seen:
                return
            seen.add(id(x))
            yield x
            for fname in getattr(x, '__dataclass_fields__', {}):
                yield from visit(getattr(x, fname, None))
        elif isinstance(x, (list, tuple)):
            for item in x:
                yield from visit(item)

    yield from visit(root)


def _resolve_import_target(importer, raw_path, line):
    """Absolute Path of the module an import string points at, or None.

    Reuses `modules.resolve_import`, so the LSP resolves exactly what the
    compiler resolves (`pkg:` via niko.lock + the package cache, relative
    `./` paths, NIKO_PATH, the bundled stdlib, cwd). Unresolvable imports
    yield None -- the definition request then reports "no definition"
    instead of failing.
    """
    try:
        return resolve_import(raw_path, str(Path(importer).parent),
                              line, importer)
    except ImportErrorNiko:
        return None


def _resolve_use_target(importer, raw_module, line):
    """Absolute Path of the file a `use` string points at, or None.

    Reuses `modules.resolve_use`, so the LSP resolves exactly what the
    compiler resolves (builtin-name uses yield None -- those names are
    builtins; the importing file's dir, NIKO_PATH, the bundled stdlib,
    cwd). Unresolvable uses yield None -- the request then reports "no
    definition", never an error.
    """
    try:
        return resolve_use(raw_module, str(Path(importer).parent),
                           line, importer)
    except ImportErrorNiko:
        return None


def _search_use_tree(importer, tree, word, seen):
    """(target Path, defining node) for `word` through the tree's
    top-level `use` statements, or None.

    Later `use` statements win on name conflicts; a used file's own
    top-level `set`/`to` beats names it pulled in through its own
    `use`s; transitive uses are followed (the compiler flattens them
    into one scope). `seen` guards against use cycles. Builtin-name
    uses are skipped -- those names are builtins.
    """
    uses = [n for n in tree.body if isinstance(n, UseStmt)]
    for n in reversed(uses):
        target = _resolve_use_target(importer, n.module, n.line)
        if target is None or target in seen:
            continue
        seen.add(target)
        node, _lines = _module_top_level(target, word)
        if node is not None:
            return target, node
        try:
            sub = parse(Path(target).read_text(encoding='utf8'))
        except (OSError, ParseError):
            continue
        found = _search_use_tree(str(target), sub, word, seen)
        if found is not None:
            return found
    return None


def _module_top_level(target, name):
    """(AST node, source lines) for the top-level `set`/`to` defining
    `name` in the module file; (None, []) when the module can't be read
    or parsed."""
    try:
        text = Path(target).read_text(encoding='utf8')
    except OSError:
        return None, []
    try:
        tree = parse(text)
    except ParseError:
        return None, []
    lines = text.splitlines()
    for n in tree.body:
        if isinstance(n, (SetStmt, FunctionDef)) and n.name == name:
            return n, lines
    return None, lines


def _module_def_line(target, name):
    """0-based line of the top-level `set`/`to` defining `name` in the
    module file; 0 when the module can't be read/parsed or doesn't define
    it (jumping to the file itself is still the right answer)."""
    node, _lines = _module_top_level(target, name)
    return node.line - 1 if node is not None else 0


def _doc_comment(lines, line1):
    """Doc-comment text (without `#`) from the comment block immediately
    above 1-based `line1`, in source order; '' when there is none.

    Same convention as the stdlib docs: consecutive `#` lines directly
    preceding a `to`/`set` line.
    """
    block = []
    i = line1 - 2  # 0-based index of the line just above
    while i >= 0 and lines[i].lstrip().startswith('#'):
        text = lines[i].lstrip()[1:]
        if text.startswith(' '):
            text = text[1:]
        block.append(text)
        i -= 1
    block.reverse()
    return '\n'.join(block).strip()


def _module_hover_text(node, lines):
    """Markdown hover body for a module-level `set`/`to`: the signature
    (same shape as the in-file hover from `symbols.py`) plus the doc
    comment from the module file."""
    if isinstance(node, FunctionDef):
        params = [p.split(':', 1)[0].strip() for p in node.params]
        sig = f'{node.name}({", ".join(params)})' + (
            f' -> {node.return_type}' if node.return_type else '')
        body = (f'**{node.name}** (func): '
                f'`{node.return_type or "unknown"}`\n\n{sig}')
    else:
        t = node.type_name or _infer(node.expr)
        body = f'**{node.name}** (var): `{t}`'
    doc = _doc_comment(lines, node.line)
    if doc:
        body += '\n\n' + doc
    return body


# ---------------------------------------------------------------------------
# Alpha 32: rename refactoring -- shared helpers.
#
# RENAME-SCOPE RULES (also documented in ALPHA32_DESIGN.md):
# 1. Renaming a local or parameter renames every reference in its scope,
#    including nested closures that capture it. Shadowing is honored: an
#    inner binding renames only the references bound to the inner one.
# 2. Renaming a top-level `set`/`to` renames its references in the file.
#    When the name is exported, the rename extends cross-file: every
#    file that does `import "x.niko" as alias` gets its `alias.name`
#    occurrences renamed (each file keeps its own alias spelling), and
#    every file that merges the name in with `use` gets its bare-name
#    references renamed. Importer discovery is read-only and bounded
#    (open documents first, then a capped walk of the project trees).
# 3. NEVER renamed: builtins, keywords, string contents, comments, the
#    quoted path in `import "..."`/`use "..."`, record keys (`{k: v}`)
#    and record fields (`rec.field`). Cursor on the attribute side of
#    `alias.name` (live import alias) renames the module's export (rule
#    2); cursor on any other attribute renames nothing. Renaming an
#    import alias renames the alias binding in-file only -- the module
#    file is never touched.
# 4. The new name must be a legal identifier, not a keyword, not a
#    builtin, and must not collide with a visible binding (same-scope
#    redefinition or a nested binding that would capture references).
#
# MECHANICS: scope resolution reuses `symbols.find_symbol` (the same
# line-oriented scope info hover/definition use), so shadowing behaves
# identically everywhere. Column precision comes from re-scanning each
# line's text with strings/comments masked out (`_mask_line`), because
# AST nodes carry lines but not columns.
def _mask_line(line):
    """`line` with string literals and comments blanked (length kept).

    Lets the rename scanner work on code only: a name inside a string
    or a comment is never a renameable occurrence. Handles both quote
    styles and backslash escapes; Niko 2 strings are single-line, so
    this is safe per line.
    """
    out = list(line)
    i, n = 0, len(line)
    while i < n:
        c = line[i]
        if c == '#':
            for j in range(i, n):
                out[j] = ' '
            break
        if c == '"' or c == "'":
            out[i] = ' '
            i += 1
            while i < n and line[i] != c:
                if line[i] == '\\' and i + 1 < n:
                    out[i] = ' '
                    out[i + 1] = ' '
                    i += 2
                    continue
                out[i] = ' '
                i += 1
            if i < n:
                out[i] = ' '
                i += 1
        else:
            i += 1
    return ''.join(out)


def _split_attribute(masked, m):
    """(object-word, attr-word) when WORD match `m` is the attribute
    side of a dotted access (`obj.attr`, whitespace around the dot is
    allowed by the parser); None otherwise."""
    j = m.start() - 1
    while j >= 0 and masked[j] in ' \t':
        j -= 1
    if j < 0 or masked[j] != '.':
        return None
    om = re.search(r'[A-Za-z_][A-Za-z0-9_]*$', masked[:j])
    if om is None:
        return None
    return om.group(0), m.group(0)


def _scan_word_occurrences(text, word):
    """Yield (line0, start, end, kind) for each code occurrence of `word`.

    Strings and comments are masked first, so occurrences inside them
    never surface. kind is 'plain' (a real name occurrence), 'attr'
    (the attribute side of `obj.word`), or 'key' (a record key `word:`
    inside braces -- record keys are not bindings and are never
    renamed). Brace depth is carried across lines so multi-line record
    literals classify correctly.
    """
    depth = 0
    for i, raw in enumerate(text.splitlines()):
        masked = _mask_line(raw)
        for m in WORD.finditer(masked):
            if m.group(0) != word:
                continue
            if _split_attribute(masked, m) is not None:
                yield i, m.start(), m.end(), 'attr'
                continue
            d = (depth + masked[:m.start()].count('{')
                 - masked[:m.start()].count('}'))
            k = m.end()
            while k < len(masked) and masked[k] in ' \t':
                k += 1
            if d > 0 and k < len(masked) and masked[k] == ':':
                yield i, m.start(), m.end(), 'key'
                continue
            yield i, m.start(), m.end(), 'plain'
        depth += masked.count('{') - masked.count('}')


def _find_scope(root, name, line):
    """(innermost Scope, Symbol) for `name` visible at 1-based `line`;
    (None, None) when no scope defines it. Same walk as
    `symbols.find_symbol`, which returns only the Symbol."""
    best = (None, None)

    def visit(scope):
        nonlocal best
        if scope.start <= line <= scope.end and name in scope.symbols:
            best = (scope, scope.symbols[name])
        for ch in scope.children:
            visit(ch)

    visit(root)
    return best


def _is_top_level_def(tree, name):
    """True when `name` is a top-level `set`/`to` -- i.e. an export."""
    return any(isinstance(n, (SetStmt, FunctionDef)) and n.name == name
               for n in tree.body)


_IDENT_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


class _RenameRefused(Exception):
    """A rename that cannot be performed; the message is sent back as
    the LSP `InvalidParams` (-32602) error, in plain English."""


_PROJECT_MARKERS = ('niko.toml', 'niko.lock', '.git')


def _project_root(start_dir):
    """Nearest ancestor of `start_dir` (inclusive) holding a project
    marker (`niko.toml`, `niko.lock`, `.git`); `start_dir` itself when
    there is none."""
    cur = Path(start_dir).resolve()
    while True:
        if any((cur / m).exists() for m in _PROJECT_MARKERS):
            return str(cur)
        parent = cur.parent
        if parent == cur:
            return str(cur)
        cur = parent


_WALK_SKIP_DIRS = {'__pycache__', 'node_modules', '.venv', 'venv', 'target',
                   'build', 'dist', '.git', '.hg', '.svn', '.niko',
                   '.idea', '.vscode'}


def _walk_niko_files(roots, limit=500):
    """Resolved `.niko` paths under `roots` (read-only walk).

    Skips hidden directories and common build/dependency dirs; capped
    at `limit` files so a huge tree cannot stall a rename request.
    """
    out = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames
                           if not d.startswith('.')
                           and d not in _WALK_SKIP_DIRS]
            for fn in filenames:
                if fn.endswith('.niko'):
                    out.append(str(Path(dirpath, fn).resolve()))
                    if len(out) >= limit:
                        return out
    return out


def _diagnostic(exc):
    line = max(1, getattr(exc, 'line', 1) or 1)
    col = getattr(exc, 'col', None) or 1
    return {
        'range': {
            'start': {'line': line - 1, 'character': max(0, col - 1)},
            'end': {'line': line - 1, 'character': max(0, col - 1) + 1},
        },
        'severity': 1,
        'source': 'niko2',
        'message': str(exc),
    }


def _has_module_stmts(tree):
    """True when the entry needs the module pipeline: a top-level
    `import` or `use` statement is present."""
    return any(isinstance(n, (ImportStmt, UseStmt)) for n in tree.body)


def _lint_alias_attrs(graph):
    """[(path, Diagnostic)] for `alias.attr` where `attr` is not one of
    the imported module's exports.

    The shared checker types every import alias as `map` (attribute
    access is `any`), so without this the LSP would stay silent on
    `m.nope` -- a name that fails at runtime. This lint is LSP-local
    and read-only: compile-time behavior is unchanged. `find_symbol`
    keeps it precise: when `m` is shadowed by a local binding at the
    attribute's line, the lint stays quiet.
    """
    out = []
    by_path = {u.path: u for u in graph}
    exports = _module_unit_exports(graph)
    for unit in graph:
        aliases = {}
        for alias, target, _line in unit.imports:
            tgt = by_path.get(target)
            if tgt is not None:
                aliases[alias] = tgt
        if not aliases:
            continue
        try:
            root = collect_symbols(unit.tree)
        except Exception:
            continue
        for node in _iter_nodes(unit.tree):
            if not (isinstance(node, AttrExpr)
                    and isinstance(node.obj, NameExpr)
                    and node.obj.name in aliases):
                continue
            sym = find_symbol(root, node.obj.name, node.line)
            if sym is None or sym.kind != 'module':
                continue  # shadowed by a local: not the import alias
            if node.name not in exports[aliases[node.obj.name].key]:
                out.append((str(unit.path), Diagnostic(
                    f'unknown attribute "{node.name}" on module '
                    f'"{node.obj.name}"',
                    line=node.line)))
    return out


class _ModuleTreeCache:
    """Per-entry mtime/size-keyed cache of parsed module trees (Alpha 26).

    DESIGN NOTE -- what is cached and when it is thrown away:

    Each entry maps a resolved module path to ``(mtime_ns, size, tree)``:
    the parsed tree is reused only while the file's mtime *and* size both
    still match. The cache feeds the module pipeline through the existing
    ``source_overrides`` mechanism, so ``build_module_graph`` needs no
    changes: a cache hit is simply a pre-parsed tree handed in as an
    override, and a miss is parsed from disk exactly as before.

    INVALIDATION RULE (simplest correct): a module counts as *changed*
    when its (mtime, size) differs from the cached key, when the file can
    no longer be stat'ed, or when an open unsaved document overrides it
    (an unsaved doc always counts as changed, and its in-memory tree is
    never stored in the cache -- otherwise a later save would serve the
    stale in-memory tree). A changed module invalidates its own entry
    *and everything downstream of it*: its importers, transitively, using
    the previous analysis's import/use edges. Over-invalidation is always
    safe (it just re-parses); under-invalidation would publish stale
    diagnostics.

    Why invalidate downstream at all, when a downstream module's own file
    didn't change? A module's diagnostics can depend on its dependencies:
    `use` merges the dependency's names into scope, and the alias-
    attribute lint reads the dependency's exports. So a dependency change
    must refresh the importers' diagnostics. Today the cached unit is the
    parsed tree, which makes the rule observable as a downstream re-parse;
    the rule stays correct if the cached unit later grows into per-module
    check results.

    The fast path (no top-level import/use) never touches this cache.
    ``last_parsed`` records the resolved paths re-parsed from disk by the
    most recent *successful* analysis -- a test/benchmark hook, not part
    of the protocol.
    """

    def __init__(self):
        self.trees = {}      # str(resolved path) -> (mtime_ns, size, tree)
        self.importers = {}  # str(resolved path) -> set(str(importer path))
        self.last_parsed = []

    def merged_overrides(self, entry_path, open_overrides):
        """Drop stale entries (changed modules + downstream importers),
        then return the effective ``source_overrides``: fresh cached trees
        with the open-document overrides winning.

        Returns None when there is nothing to override, so
        ``build_module_graph`` behaves exactly as without a cache.
        """
        changed = set()
        for pstr, (mtime_ns, size, _tree) in list(self.trees.items()):
            try:
                st = os.stat(pstr)
            except OSError:
                changed.add(pstr)
                continue
            if (st.st_mtime_ns, st.st_size) != (mtime_ns, size):
                changed.add(pstr)
        if open_overrides:
            # An unsaved document always counts as changed; its tree is
            # used as-is and never stored in the cache.
            for p in open_overrides:
                changed.add(str(Path(p).resolve()))
        # Invalidate everything downstream: importers, transitively.
        doomed = set(changed)
        stack = list(changed)
        while stack:
            cur = stack.pop()
            for parent in self.importers.get(cur, ()):
                if parent not in doomed:
                    doomed.add(parent)
                    stack.append(parent)
        for pstr in doomed:
            self.trees.pop(pstr, None)
        merged = {Path(pstr): tree
                  for pstr, (_m, _s, tree) in self.trees.items()}
        if open_overrides:
            merged.update(open_overrides)
        return merged or None

    def commit(self, graph, open_overrides):
        """Record this analysis's results: fresh (mtime, size, tree) for
        every disk-backed module, the new importer edges, and prune
        modules that are no longer reachable."""
        skip = ({str(Path(p).resolve()) for p in open_overrides}
                if open_overrides else set())
        importers = {}
        live = set()
        for unit in graph:
            pstr = str(unit.path)
            live.add(pstr)
            for _alias, target, _line in unit.imports:
                importers.setdefault(str(target), set()).add(pstr)
            for _raw, target, _line in unit.uses:
                if target is not None:
                    importers.setdefault(str(target), set()).add(pstr)
            if unit.kind == 'entry' or pstr in skip:
                continue
            try:
                st = os.stat(pstr)
            except OSError:
                continue
            self.trees[pstr] = (st.st_mtime_ns, st.st_size, unit.tree)
        self.importers = importers
        for pstr in list(self.trees):
            if pstr not in live:
                del self.trees[pstr]


def _analyze(text, entry_path=None, source_overrides=None, module_cache=None):
    """Return ({absolute path: [diagnostics]}, entry tree or None).

    Alpha 23: when the entry file has top-level `import`/`use`
    statements, it is checked through the module pipeline
    (`build_module_graph` + `check_units`) instead of the old
    single-file check, so import aliases and `use`d names resolve
    exactly as they do at compile time. Diagnostics are grouped by
    file, so errors inside module files attribute to the right
    file/line. `source_overrides` supplies in-memory trees for other
    open documents (unsaved edits included); anything not overridden
    is read from disk, read-only -- diagnostics never write files and
    never touch the network (package-cache reads are local).

    `module_cache` is an optional `_ModuleTreeCache` (one per entry file,
    owned by the Server): unchanged modules are not re-parsed on every
    analysis -- their trees are handed to the module pipeline through
    `source_overrides`, keyed on each file's (mtime, size). A changed
    module invalidates its own entry and everything downstream of it
    (its importers, transitively); see the class design note.

    Without module statements -- or without a file path, as for an
    unsaved document -- this is the old single-file check: no disk
    reads at all, and the cache is untouched.
    """
    if entry_path is not None:
        entry_path = str(Path(entry_path).resolve())
    diags = {}

    def add(path, exc):
        diags.setdefault(str(path), []).append(_diagnostic(exc))

    try:
        tree = parse(text)
    except ParseError as e:
        e.path = entry_path
        add(entry_path or '<memory>', e)
        return diags, None
    if entry_path is None or not _has_module_stmts(tree):
        try:
            check(tree, [])
        except TypeErrorNiko as e:
            add(entry_path or '<memory>', e)
        return diags, tree
    open_overrides = source_overrides
    cache_before = None
    if module_cache is not None:
        source_overrides = module_cache.merged_overrides(entry_path,
                                                         open_overrides)
        cache_before = set(module_cache.trees)
    try:
        graph = build_module_graph(entry_path, entry_tree=tree,
                                   source_overrides=source_overrides)
    except ImportErrorNiko as e:
        # Unresolvable import/use, or an import/use cycle: exactly one
        # diagnostic, on the offending line in the importing file, and
        # no cascade of follow-on errors.
        add(e.path or entry_path, e)
        return diags, tree
    except ParseError as e:
        add(e.path or entry_path, e)
        return diags, tree
    if module_cache is not None:
        # The graph built cleanly: record the fresh (mtime, size, tree)
        # entries and the new importer edges. Entries re-parsed this
        # round are the ones that were invalidated before the build.
        module_cache.commit(graph, open_overrides)
        module_cache.last_parsed = sorted(set(module_cache.trees)
                                          - cache_before)
    try:
        check_units(graph)
    except TypeErrorNiko as e:
        add(e.path or entry_path, e)
        return diags, tree
    for path, attr_exc in _lint_alias_attrs(graph):
        add(path, attr_exc)
    return diags, tree


class Server:
    def __init__(self, stdin, stdout):
        self.stdin = stdin
        self.stdout = stdout
        self.docs = {}          # uri -> text
        self.trees = {}         # uri -> parsed tree (or None)
        self._diags_owner = {}  # uri -> root uri whose analysis last
                                # published diagnostics for it
        self._module_caches = {}  # resolved entry path -> _ModuleTreeCache
                                  # (Alpha 26: mtime/size-keyed parsed
                                  # module trees, one cache per entry)
        self._id_seq = 0

    def send(self, obj):
        write_message(self.stdout, obj)

    def _open_overrides(self, uri):
        """{resolved Path: parsed tree} for every other open document
        that parses, so module-aware diagnostics see unsaved edits in
        module files instead of their on-disk copies. A document that
        fails to parse is left out -- its own diagnostics already show
        the parse error, and the graph falls back to the disk copy."""
        overrides = {}
        for ouri, otext in self.docs.items():
            if ouri == uri:
                continue
            opath = _uri_to_path(ouri)
            if opath is None:
                continue
            try:
                otree = parse(otext)
            except ParseError:
                continue
            overrides[Path(opath).resolve()] = otree
        return overrides or None

    def _notify_diagnostics(self, uri):
        text = self.docs.get(uri, '')
        entry_path = _uri_to_path(uri)
        overrides = None
        module_cache = None
        if entry_path is not None:
            # Only parse the other open documents when the entry
            # actually needs the module pipeline.
            try:
                quick = parse(text)
            except ParseError:
                quick = None
            if quick is not None and _has_module_stmts(quick):
                overrides = self._open_overrides(uri)
                key = str(Path(entry_path).resolve())
                module_cache = self._module_caches.setdefault(
                    key, _ModuleTreeCache())
        diags_by_path, tree = _analyze(text, entry_path, overrides,
                                       module_cache)
        self.trees[uri] = tree
        resolved_entry = (str(Path(entry_path).resolve())
                          if entry_path is not None else None)
        publish = {}
        for path, ds in diags_by_path.items():
            if path == resolved_entry or (
                    resolved_entry is None and path == '<memory>'):
                publish[uri] = ds
            elif path != '<memory>':
                publish[_path_to_uri(path)] = ds
        publish.setdefault(uri, [])
        for u, ds in publish.items():
            self.send({
                'jsonrpc': '2.0',
                'method': 'textDocument/publishDiagnostics',
                'params': {'uri': u, 'diagnostics': ds},
            })
            self._diags_owner[u] = uri
        # Clear diagnostics this document's analysis published before
        # that are gone now (a fixed import, a clean module file, ...).
        # Diagnostics owned by another open document's analysis are
        # left alone -- that analysis refreshes them when its own
        # document changes.
        for u, owner in list(self._diags_owner.items()):
            if owner == uri and u not in publish:
                self.send({
                    'jsonrpc': '2.0',
                    'method': 'textDocument/publishDiagnostics',
                    'params': {'uri': u, 'diagnostics': []},
                })
                del self._diags_owner[u]

    def _tree(self, uri):
        tree = self.trees.get(uri)
        if tree is None:
            _diags, tree = _analyze(self.docs.get(uri, ''))
            self.trees[uri] = tree
        return tree

    def _symbols(self, uri):
        tree = self._tree(uri)
        if tree is None:
            return None
        return collect_symbols(tree)

    def handle(self, msg):
        method = msg.get('method')
        mid = msg.get('id')
        params = msg.get('params', {})

        def respond(result):
            if mid is not None:
                self.send({'jsonrpc': '2.0', 'id': mid, 'result': result})

        try:
            if method == 'initialize':
                respond({'capabilities': {
                    'textDocumentSync': 1,
                    'completionProvider': {},
                    'hoverProvider': True,
                    'definitionProvider': True,
                    'documentFormattingProvider': True,
                    'renameProvider': {'prepareProvider': True},
                }, 'serverInfo': {'name': 'niko2-lsp', 'version': VERSION}})
            elif method == 'initialized':
                pass
            elif method == 'shutdown':
                respond(None)
            elif method == 'textDocument/didOpen':
                doc = params['textDocument']
                self.docs[doc['uri']] = doc['text']
                self.trees.pop(doc['uri'], None)
                self._notify_diagnostics(doc['uri'])
            elif method == 'textDocument/didChange':
                uri = params['textDocument']['uri']
                for change in params.get('contentChanges', []):
                    self.docs[uri] = change['text']
                self.trees.pop(uri, None)
                self._notify_diagnostics(uri)
            elif method == 'textDocument/didClose':
                self.docs.pop(params['textDocument']['uri'], None)
                self.trees.pop(params['textDocument']['uri'], None)
            elif method == 'textDocument/completion':
                respond(self._complete(params))
            elif method == 'textDocument/hover':
                respond(self._hover(params))
            elif method == 'textDocument/definition':
                respond(self._definition(params))
            elif method == 'textDocument/prepareRename':
                respond(self._prepare_rename(params))
            elif method == 'textDocument/rename':
                respond(self._rename(params))
            elif method == 'textDocument/formatting':
                respond(self._format(params))
            elif mid is not None and method not in ('exit',):
                self.send({'jsonrpc': '2.0', 'id': mid,
                           'error': {'code': -32601, 'message': f'method not found: {method}'}})
            if method == 'exit':
                return False
        except _RenameRefused as e:
            if mid is not None:
                self.send({'jsonrpc': '2.0', 'id': mid,
                           'error': {'code': -32602, 'message': str(e)}})
        except Exception:
            if mid is not None:
                self.send({'jsonrpc': '2.0', 'id': mid,
                           'error': {'code': -32603, 'message': traceback.format_exc(limit=3)}})
        return True

    # -- feature implementations --------------------------------------

    def _complete_pkg_import(self, uri, pos):
        """Completion items for `import "pkg:…"` strings (Alpha 26), or
        None when the cursor is not inside such a string.

        - `import "pkg:|` / `import "pkg:par|` -> installed package
          names from the local package cache (`$NIKO_PKG_CACHE`
          honored), filtered by the typed prefix.
        - `import "pkg:name/|` / `import "pkg:name/sub/pa|` -> `.niko`
          files inside that package, as relative paths, filtered by
          the typed remainder.

        Plain (non-`pkg:`) import strings are untouched (None). An empty
        or unresolvable cache yields [], never an error. The network is
        never consulted -- package listing and resolution are
        cache-local by construction.
        """
        line = pos.get('line')
        char = pos.get('character')
        if line is None or char is None:
            return None
        lines = self.docs.get(uri, '').splitlines()
        if line >= len(lines):
            return None
        prefix = lines[line][:char]
        m = _PKG_IMPORT_RE.search(prefix)
        if m is None:
            return None
        hash_i = prefix.find('#')
        if hash_i != -1 and hash_i < m.start():
            return None  # inside a comment, not an import string
        rest = m.group(2)
        if '/' not in rest:
            return [{'label': name, 'kind': 9,
                     'detail': (f'Niko package {ver}' if ver
                                else 'Niko package')}
                    for name, ver in _cached_package_names()
                    if name.startswith(rest)]
        name, _sep, sub = rest.partition('/')
        pkg_dir = _pkg_dir_for_completion(uri, name)
        if pkg_dir is None:
            return []
        items = []
        try:
            files = sorted(pkg_dir.rglob('*.niko'))
        except OSError:
            return []
        for f in files:
            rel = f.relative_to(pkg_dir).as_posix()
            if rel.startswith(sub):
                items.append({'label': rel, 'kind': 17,
                              'detail': f'pkg:{name}'})
        return items

    def _complete(self, params):
        uri = params['textDocument']['uri']
        pkg_items = self._complete_pkg_import(uri,
                                              params.get('position') or {})
        if pkg_items is not None:
            return pkg_items
        items = [{'label': k, 'kind': 14} for k in KEYWORDS]
        for name in sorted(BUILTIN_NAMES):
            items.append({'label': name, 'kind': 3,
                          'detail': BUILTIN_DOCS.get(name, 'Niko builtin')})
        root = self._symbols(uri)
        if root is not None:
            for name, sym in sorted(all_names(root).items()):
                items.append({'label': name, 'kind': 6,
                              'detail': f'{sym.kind}: {sym.type_str}'})
        return items

    def _hover(self, params):
        uri = params['textDocument']['uri']
        pos = params['position']
        lines = self.docs.get(uri, '').splitlines()
        if pos['line'] >= len(lines):
            return None
        word = _word_at(lines[pos['line']], pos['character'])
        if not word:
            return None
        if word in BUILTIN_DOCS:
            return {'contents': {'kind': 'markdown', 'value': f'`{word}` — {BUILTIN_DOCS[word]}'}}
        tree = self._tree(uri)
        if tree is not None:
            hov = self._hover_import(uri, tree, lines[pos['line']],
                                     pos['line'] + 1,
                                     pos['character'], word)
            if hov is not None:
                return hov
        root = self._symbols(uri)
        if root is not None:
            sym = find_symbol(root, word, pos['line'] + 1)
            if sym is not None:
                body = f'**{sym.name}** ({sym.kind}): `{sym.type_str}`'
                if sym.doc:
                    body += f'\n\n{sym.doc}'
                return {'contents': {'kind': 'markdown', 'value': body}}
        # Cross-file `use`: only when the name isn't defined in this file.
        if tree is not None:
            hov = self._hover_use(uri, tree, pos['line'] + 1, word)
            if hov is not None:
                return hov
        return None

    def _definition(self, params):
        uri = params['textDocument']['uri']
        pos = params['position']
        lines = self.docs.get(uri, '').splitlines()
        if pos['line'] >= len(lines):
            return None
        word = _word_at(lines[pos['line']], pos['character'])
        if not word:
            return None
        tree = self._tree(uri)
        if tree is not None:
            loc = self._definition_import(uri, tree, lines[pos['line']],
                                          pos['line'] + 1,
                                          pos['character'], word)
            if loc is not None:
                return loc
        root = self._symbols(uri)
        if root is not None:
            sym = find_symbol(root, word, pos['line'] + 1)
            if sym is not None and sym.kind != 'builtin':
                return _loc(uri, sym.line - 1)
        # Cross-file `use`: only when the name isn't defined in this file
        # (the entry's own definitions always win over used names).
        if tree is not None:
            loc = self._definition_use(uri, tree, lines[pos['line']],
                                       pos['line'] + 1,
                                       pos['character'], word)
            if loc is not None:
                return loc
        return None

    def _definition_import(self, uri, tree, line_text, line1, char, word):
        """Cross-file go-to-definition through `import` (Alpha 17).

        - Cursor on the quoted path of `import "path.niko" as alias`:
          jump to the module file itself.
        - Cursor on `name` in `alias.name` where `alias` is an import
          alias: jump to the top-level `set`/`to` defining `name` in the
          module file.
        Returns None when the cursor isn't on either, or when the import
        can't be resolved (missing file, bad `pkg:` spec): the request
        then reports "no definition", never an error.
        """
        importer = _uri_to_path(uri)
        if importer is None:
            return None
        imports = {}  # alias -> raw path string
        for n in _iter_nodes(tree):
            if isinstance(n, ImportStmt):
                imports[n.alias] = n.path
                if n.line == line1:
                    for quote in ('"', "'"):
                        q = f'{quote}{n.path}{quote}'
                        i = line_text.find(q)
                        if i != -1 and i <= char < i + len(q):
                            target = _resolve_import_target(
                                importer, n.path, n.line)
                            if target is None:
                                return None
                            return _loc(_path_to_uri(target), 0)
        for n in _iter_nodes(tree):
            if (isinstance(n, AttrExpr) and n.line == line1
                    and isinstance(n.obj, NameExpr)
                    and n.obj.name in imports and n.name == word):
                target = _resolve_import_target(
                    importer, imports[n.obj.name], n.line)
                if target is None:
                    return None
                return _loc(_path_to_uri(target),
                            _module_def_line(target, word))
        return None

    def _hover_import(self, uri, tree, line_text, line1, char, word):
        """Cross-file hover through `import` (Alpha 21).

        Cursor on `name` in `alias.name` where `alias` is an import
        alias: render the target's signature and doc comment from the
        module file (same resolution as `_definition_import`, so `pkg:`,
        relative `./`, NIKO_PATH, and the bundled stdlib all work).
        Returns None when the cursor isn't on such an attribute, the
        module doesn't define the name, or the import can't be resolved:
        the request then reports "no hover", never an error.
        """
        importer = _uri_to_path(uri)
        if importer is None:
            return None
        imports = {}
        for n in _iter_nodes(tree):
            if isinstance(n, ImportStmt):
                imports[n.alias] = n.path
        for n in _iter_nodes(tree):
            if (isinstance(n, AttrExpr) and n.line == line1
                    and isinstance(n.obj, NameExpr)
                    and n.obj.name in imports and n.name == word):
                target = _resolve_import_target(
                    importer, imports[n.obj.name], n.line)
                if target is None:
                    return None
                node, src_lines = _module_top_level(target, word)
                if node is None:
                    return None
                return {'contents': {'kind': 'markdown',
                                     'value': _module_hover_text(node,
                                                                 src_lines)}}
        return None

    def _definition_use(self, uri, tree, line_text, line1, char, word):
        """Cross-file go-to-definition through `use` (Alpha 22).

        - Cursor on the quoted path of `use "…"`:
          jump to the used file itself.
        - Cursor on a bare name the file gets through `use` (the caller
          checks the name isn't defined in this file first -- the entry's
          own definitions always win over used names): jump to the
          top-level `set`/`to` defining it in the used file.
        Returns None when the cursor isn't on either, the name isn't
        provided by any `use`, or the use can't be resolved (builtin-name
        use, missing file): the request then reports "no definition",
        never an error.
        """
        importer = _uri_to_path(uri)
        if importer is None:
            return None
        for n in tree.body:
            if isinstance(n, UseStmt) and n.line == line1:
                q = n.module.strip()
                i = line_text.find(q)
                if i != -1 and i <= char < i + len(q):
                    target = _resolve_use_target(importer, n.module, n.line)
                    if target is None:
                        return None
                    return _loc(_path_to_uri(target), 0)
        found = _search_use_tree(importer, tree, word, set())
        if found is None:
            return None
        target, node = found
        return _loc(_path_to_uri(target), node.line - 1)

    def _hover_use(self, uri, tree, line1, word):
        """Cross-file hover through `use` (Alpha 22).

        Cursor on a bare name the file gets through `use`: render the
        target's signature and doc comment from the used file (same
        resolution as `_definition_use`). In-file definitions win -- the
        caller checks those first. Returns None when the name isn't
        provided by any `use` or the use can't be resolved: the request
        then reports "no hover", never an error.
        """
        importer = _uri_to_path(uri)
        if importer is None:
            return None
        found = _search_use_tree(importer, tree, word, set())
        if found is None:
            return None
        target, _node = found
        node, src_lines = _module_top_level(target, word)
        if node is None:
            return None
        return {'contents': {'kind': 'markdown',
                             'value': _module_hover_text(node, src_lines)}}

    # -- Alpha 32: rename refactoring ---------------------------------

    def _parse_quiet(self, text):
        # Broad catch: this scans arbitrary project files during
        # importer discovery, and the parser can raise non-ParseError
        # on some malformed inputs (e.g. multi-line record literals).
        # Such files are simply skipped as importers; the invoking
        # file still goes through self._tree (same as hover/definition).
        try:
            return parse(text)
        except Exception:
            return None

    def _import_targets(self, importer_path, tree):
        """[(alias, resolved path str)] for the file's imports;
        unresolvable imports are skipped (their diagnostics say why)."""
        out = []
        for n in _iter_nodes(tree):
            if isinstance(n, ImportStmt):
                t = _resolve_import_target(importer_path, n.path, n.line)
                if t is not None:
                    out.append((n.alias, str(t)))
        return out

    def _use_targets(self, importer_path, tree):
        """[resolved path str] for the file's `use`s; unresolvable and
        builtin-name uses are skipped."""
        out = []
        for n in _iter_nodes(tree):
            if isinstance(n, UseStmt):
                t = _resolve_use_target(importer_path, n.module, n.line)
                if t is not None:
                    out.append(str(t))
        return out

    def _load_export_binding(self, deffile, name):
        """Binding dict for the top-level `set`/`to` `name` in `deffile`.

        Prefers the open buffer over disk (unsaved edits win); None
        when the file can't be read/parsed or doesn't define the name.
        """
        text = None
        duri = None
        for ouri, otext in self.docs.items():
            op = _uri_to_path(ouri)
            if op is not None and str(Path(op).resolve()) == deffile:
                text, duri = otext, ouri
                break
        if text is None:
            try:
                text = Path(deffile).read_text(encoding='utf8')
            except OSError:
                return None
        tree = self._parse_quiet(text)
        if tree is None or not _is_top_level_def(tree, name):
            return None
        root = collect_symbols(tree)
        node = next(n for n in tree.body
                    if isinstance(n, (SetStmt, FunctionDef))
                    and n.name == name)
        sym = find_symbol(root, name, node.line)
        if sym is None:
            return None
        return {'kind': 'export', 'word': name, 'file': deffile,
                'uri': duri, 'text': text, 'root': root, 'sym': sym,
                'scope': root}

    def _locate_rename_target(self, uri, line0, char):
        """(target, word, span-or-reason) for a rename at a position.

        Returns (target, word, span) with span=(line0, start, end) when
        the cursor is on something renameable, else (None, word_or_None,
        reason). Uses the open buffer (unsaved edits included) through
        `self._tree`, exactly like hover/definition.
        """
        text = self.docs.get(uri)
        if text is None:
            return None, None, 'that file is not open in the editor'
        lines = text.splitlines()
        if line0 >= len(lines):
            return None, None, 'there is nothing to rename at that position'
        masked = _mask_line(lines[line0])
        m = next((mm for mm in WORD.finditer(masked)
                  if mm.start() <= char <= mm.end()), None)
        if m is None:
            return None, None, 'there is nothing to rename at that position'
        word = m.group(0)
        span = (line0, m.start(), m.end())
        if word in KEYWORDS:
            return (None, word,
                    f'"{word}" is a keyword, and keywords cannot be renamed')
        if word in BUILTIN_NAMES:
            return (None, word,
                    f'"{word}" is a builtin, and builtins cannot be renamed')
        tree = self._tree(uri)
        if tree is None:
            return (None, word,
                    'the file has errors, so rename is unavailable '
                    'until they are fixed')
        root = collect_symbols(tree)
        line1 = line0 + 1
        attr = _split_attribute(masked, m)
        if attr is not None:
            return self._locate_attribute_target(uri, tree, root, line1,
                                                 attr[0], attr[1], span)
        sym = find_symbol(root, word, line1)
        if sym is None:
            importer = _uri_to_path(uri)
            if importer is not None:
                found = _search_use_tree(importer, tree, word, set())
                if found is not None:
                    return (None, word,
                            f'"{word}" comes from a file merged in with '
                            '`use`; renaming across `use` from the using '
                            'file is not supported yet -- open the '
                            'defining file to rename it there')
            return (None, word,
                    f'"{word}" is not defined here, so there is nothing '
                    'to rename')
        scope, _ = _find_scope(root, word, line1)
        fpath = _uri_to_path(uri)
        fpath = str(Path(fpath).resolve()) if fpath else None
        base = {'word': word, 'file': fpath, 'uri': uri, 'text': text,
                'root': root, 'sym': sym, 'scope': scope}
        if sym.kind == 'module':
            # An import alias: renameable in this file only. The module
            # file itself is never touched.
            return dict(base, kind='alias'), word, span
        if scope is root and _is_top_level_def(tree, word):
            # A top-level set/to: an export. References in this file
            # rename with it; importing files are handled cross-file by
            # _rename_export.
            return dict(base, kind='export'), word, span
        return dict(base, kind='local'), word, span

    def _locate_attribute_target(self, uri, tree, root, line1, objword,
                                 attrword, span):
        """Target for a cursor on the attribute side of `obj.attr`.

        Only `alias.name` where `alias` is a live import alias resolves
        to a renameable binding (the module's export); record fields
        and shadowed aliases are not renameable.
        """
        imports = {}
        for n in _iter_nodes(tree):
            if isinstance(n, ImportStmt):
                imports[n.alias] = (n.path, n.line)
        if objword not in imports:
            return (None, attrword,
                    f'"{attrword}" is a field, not a renameable name')
        osym = find_symbol(root, objword, line1)
        if osym is None or osym.kind != 'module':
            return (None, attrword,
                    f'"{objword}" is shadowed here, so '
                    f'"{objword}.{attrword}" does not refer to the '
                    'module import')
        importer = _uri_to_path(uri)
        raw_path, iline = imports[objword]
        tgt = (_resolve_import_target(importer, raw_path, iline)
               if importer else None)
        if tgt is None:
            return None, attrword, f'the module "{raw_path}" cannot be found'
        binding = self._load_export_binding(str(tgt), attrword)
        if binding is None:
            return None, attrword, \
                f'the module does not export "{attrword}"'
        return binding, attrword, span

    def _prepare_rename(self, params):
        uri = params['textDocument']['uri']
        pos = params.get('position') or {}
        target, word, span = self._locate_rename_target(
            uri, pos.get('line', 0), pos.get('character', 0))
        if target is None or word is None:
            return None
        line0, s, e = span
        return {'range': {'start': {'line': line0, 'character': s},
                          'end': {'line': line0, 'character': e}},
                'placeholder': word}

    @staticmethod
    def _text_edit(span, new_name):
        line0, s, e = span
        return {'range': {'start': {'line': line0, 'character': s},
                          'end': {'line': line0, 'character': e}},
                'newText': new_name}

    def _plain_edits(self, text, root, sym):
        """[(line0, start, end)] for every plain code occurrence of
        `sym.name` that resolves to `sym`.

        Attribute sides (`obj.name`) and record keys (`name:` in braces)
        are never plain occurrences; shadowing falls out of the
        scope-aware `find_symbol` check, so an inner binding's
        references never leak into an outer rename and vice versa.
        """
        edits = []
        for line0, s, e, kind in _scan_word_occurrences(text, sym.name):
            if kind != 'plain':
                continue
            if find_symbol(root, sym.name, line0 + 1) is not sym:
                continue
            edits.append((line0, s, e))
        return edits

    def _check_collision(self, root, scope, new_name, lines1):
        """Refuse the rename when `new_name` would collide with a
        visible binding; raises _RenameRefused (plain English).

        Two checks: (1) `new_name` must not already be bound in the
        target's own scope; (2) at every renamed line, the innermost
        scope that already defines `new_name` must be the target's
        scope itself (or none) -- otherwise a nested binding would
        capture the renamed references or references would resolve
        somewhere new.
        """
        if new_name in scope.symbols:
            raise _RenameRefused(
                f'"{new_name}" is already defined in that scope')
        for l1 in lines1:
            s, _ = _find_scope(root, new_name, l1)
            if s is not None and s is not scope:
                raise _RenameRefused(
                    f'"{new_name}" would clash with another "{new_name}" '
                    'visible where it is used')

    def _is_managed(self, deffile):
        """True when `deffile` is a managed artifact (the bundled
        stdlib or the local package cache): rename stays in-file there
        and never reaches across files."""
        try:
            sd = str(Path(stdlib_dir()).resolve())
            if deffile == sd or deffile.startswith(sd + os.sep):
                return True
        except Exception:
            pass
        try:
            cr = str(Path(_packages.default_cache_root()).resolve())
            if deffile == cr or deffile.startswith(cr + os.sep):
                return True
        except Exception:
            pass
        return False

    def _find_importers(self, deffile, entry_path):
        """[(kind, path, alias, uri_or_None, text, tree)] for every file
        that imports (`kind='import'`, with its alias) or `use`s
        (`kind='use'`) the definition file.

        Open documents come first (unsaved buffers included); then a
        bounded read-only walk of the project trees of the definition
        file and the entry file. Files that fail to parse are skipped
        -- their diagnostics already say why.
        """
        found = []
        seen = {deffile}
        for ouri, otext in self.docs.items():
            op = _uri_to_path(ouri)
            if op is None:
                continue
            rp = str(Path(op).resolve())
            if rp in seen:
                continue
            seen.add(rp)
            tree = self._parse_quiet(otext)
            if tree is None:
                continue
            for alias, t in self._import_targets(rp, tree):
                if t == deffile:
                    found.append(('import', rp, alias, ouri, otext, tree))
            for t in self._use_targets(rp, tree):
                if t == deffile:
                    found.append(('use', rp, None, ouri, otext, tree))
        roots = {_project_root(str(Path(deffile).parent))}
        if entry_path:
            roots.add(_project_root(str(Path(entry_path).parent)))
        for path in _walk_niko_files(sorted(roots)):
            if path in seen:
                continue
            seen.add(path)
            try:
                otext = Path(path).read_text(encoding='utf8')
            except OSError:
                continue
            tree = self._parse_quiet(otext)
            if tree is None:
                continue
            for alias, t in self._import_targets(path, tree):
                if t == deffile:
                    found.append(('import', path, alias, None, otext, tree))
            for t in self._use_targets(path, tree):
                if t == deffile:
                    found.append(('use', path, None, None, otext, tree))
        return found

    def _importer_attr_edits(self, itext, itree, ipath, iroot, alias,
                             deffile, word):
        """[(line0, start, end)] of `word` in `alias.word` occurrences
        in an importing file, where `alias` really is the import alias
        at that line (shadowed lines are skipped, mirroring the
        alias-attribute lint)."""
        targets = dict(self._import_targets(ipath, itree))
        if targets.get(alias) != deffile:
            return []
        pat = re.compile(r'\b' + re.escape(alias) + r'\s*\.\s*('
                         + re.escape(word) + r')\b')
        edits = []
        for i, raw in enumerate(itext.splitlines()):
            masked = _mask_line(raw)
            asym = find_symbol(iroot, alias, i + 1)
            if asym is None or asym.kind != 'module':
                continue
            for m in pat.finditer(masked):
                edits.append((i, m.start(1), m.end(1)))
        return edits

    def _use_importer_edits(self, itext, ipath, itree, iroot, word,
                            deffile):
        """[(line0, start, end)] of bare `word` occurrences in a file
        that merges the definition file in with `use`, resolving to the
        export.

        The entry's own definitions (and any shadowing loop/pattern
        bindings) win -- those occurrences are skipped; the use tree is
        consulted exactly as go-to-definition does, so later-`use`-wins
        and transitivity match compile time.
        """
        edits = []
        for line0, s, e, kind in _scan_word_occurrences(itext, word):
            if kind != 'plain':
                continue
            if find_symbol(iroot, word, line0 + 1) is not None:
                continue
            found = _search_use_tree(ipath, itree, word, set())
            if found is None:
                continue
            t, node = found
            if str(t) != deffile or node.name != word:
                continue
            edits.append((line0, s, e))
        return edits

    def _rename_export(self, uri, target, word, new_name):
        """WorkspaceEdit for renaming an exported top-level `set`/`to`.

        The definition file gets its definition plus every in-file
        reference; every importing file gets its `alias.<name>`
        occurrences (each file keeps its own alias spelling); every
        file that merges the name in with `use` gets its bare-name
        references.
        """
        deffile = target['file']
        edits = self._plain_edits(target['text'], target['root'],
                                  target['sym'])
        self._check_collision(target['root'], target['scope'], new_name,
                              [target['sym'].line]
                              + [e[0] + 1 for e in edits])
        duri = target['uri'] or _path_to_uri(deffile)
        changes = {duri: [self._text_edit(e, new_name) for e in edits]}
        if not self._is_managed(deffile):
            entry_path = _uri_to_path(uri)
            for imp_kind, ipath, alias, iuri, itext, itree in \
                    self._find_importers(deffile, entry_path):
                iuri = iuri or _path_to_uri(ipath)
                iroot = collect_symbols(itree)
                if imp_kind == 'import':
                    iedits = self._importer_attr_edits(
                        itext, itree, ipath, iroot, alias, deffile, word)
                else:
                    iedits = self._use_importer_edits(
                        itext, ipath, itree, iroot, word, deffile)
                if iedits:
                    changes.setdefault(iuri, []).extend(
                        self._text_edit(e, new_name) for e in iedits)
        for uri_edits in changes.values():
            uri_edits.sort(key=lambda e: (e['range']['start']['line'],
                                          e['range']['start']['character']))
        return {'changes': changes}

    def _rename(self, params):
        uri = params['textDocument']['uri']
        pos = params.get('position') or {}
        new_name = params.get('newName', '')
        if not new_name or not _IDENT_RE.match(new_name):
            raise _RenameRefused(
                f'"{new_name}" is not a valid Niko name: use letters, '
                'digits and underscores, not starting with a digit')
        if new_name in KEYWORDS:
            raise _RenameRefused(
                f'"{new_name}" is a keyword and cannot be used as a name')
        if new_name in BUILTIN_NAMES:
            raise _RenameRefused(
                f'"{new_name}" is a builtin and cannot be used as a name')
        target, word, detail = self._locate_rename_target(
            uri, pos.get('line', 0), pos.get('character', 0))
        if target is None:
            # Never a silent no-op: a non-renameable cursor is an error.
            raise _RenameRefused(detail)
        if new_name == word:
            return {'changes': {}}
        if target['kind'] in ('local', 'alias'):
            edits = self._plain_edits(target['text'], target['root'],
                                      target['sym'])
            self._check_collision(target['root'], target['scope'],
                                  new_name,
                                  [target['sym'].line]
                                  + [e[0] + 1 for e in edits])
            tedits = [self._text_edit(e, new_name) for e in edits]
            tedits.sort(key=lambda e: (e['range']['start']['line'],
                                       e['range']['start']['character']))
            return {'changes': {uri: tedits}}
        return self._rename_export(uri, target, word, new_name)

    def _format(self, params):
        uri = params['textDocument']['uri']
        text = self.docs.get(uri, '')
        try:
            tree = parse(text)
        except ParseError:
            return None
        new_text = format_program(tree)
        return [{'range': {
            'start': {'line': 0, 'character': 0},
            'end': {'line': len(text.splitlines()), 'character': 0}},
            'newText': new_text}]

    def serve(self):
        while True:
            msg = read_message(self.stdin)
            if msg is None:
                break
            if self.handle(msg) is False:
                break


def main():
    server = Server(sys.stdin.buffer, sys.stdout.buffer)
    server.serve()
    return 0
