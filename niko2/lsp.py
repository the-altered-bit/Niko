"""Niko 2 language server (LSP over stdio).

Start it with `niko2 lsp`. It speaks enough of the Language Server
Protocol for a good editing experience in any LSP-capable editor:

- `textDocument/publishDiagnostics` — parse errors and type errors, with
  the line/column positions from `niko2/diagnostics.py`
- `textDocument/completion` — keywords, builtins, and names defined in
  the file
- `textDocument/hover` — builtin docs and inferred types of your names;
  follows `import` across files (Alpha 21: `alias.name` shows the
  target's signature and doc comment from the module file)
- `textDocument/definition` — jump to where a name was defined, following
  `import` across files (Alpha 17: `alias.name` jumps to the top-level
  `set`/`to` in the module file; the import's path string jumps to the
  module file itself)
- `textDocument/formatting` — runs `niko2 format` on the whole document

Hover and go-to-definition are line-oriented: Niko 2 AST nodes carry line
numbers but not columns, so results resolve to the nearest sensible line
(see `niko2/symbols.py`). Cross-file definition reuses
`niko2/modules.py`'s import resolution, so `pkg:` imports, relative
`./` imports inside packages, NIKO_PATH, and the bundled stdlib all work
exactly as they do at compile time; an import that can't be resolved
simply yields no definition (never an error).
"""
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
from .ast import Node, ImportStmt, AttrExpr, NameExpr, SetStmt, FunctionDef
from .modules import resolve_import, ImportErrorNiko

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


def _analyze(text):
    """Return (diagnostics, tree-or-None)."""
    try:
        tree = parse(text)
    except ParseError as e:
        return [_diagnostic(e)], None
    try:
        check(tree, [])
    except TypeErrorNiko as e:
        return [_diagnostic(e)], tree
    return [], tree


class Server:
    def __init__(self, stdin, stdout):
        self.stdin = stdin
        self.stdout = stdout
        self.docs = {}          # uri -> text
        self.trees = {}         # uri -> parsed tree (or None)
        self._id_seq = 0

    def send(self, obj):
        write_message(self.stdout, obj)

    def _notify_diagnostics(self, uri):
        text = self.docs.get(uri, '')
        diags, tree = _analyze(text)
        self.trees[uri] = tree
        self.send({
            'jsonrpc': '2.0',
            'method': 'textDocument/publishDiagnostics',
            'params': {'uri': uri, 'diagnostics': diags},
        })

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
            elif method == 'textDocument/formatting':
                respond(self._format(params))
            elif mid is not None and method not in ('exit',):
                self.send({'jsonrpc': '2.0', 'id': mid,
                           'error': {'code': -32601, 'message': f'method not found: {method}'}})
            if method == 'exit':
                return False
        except Exception:
            if mid is not None:
                self.send({'jsonrpc': '2.0', 'id': mid,
                           'error': {'code': -32603, 'message': traceback.format_exc(limit=3)}})
        return True

    # -- feature implementations --------------------------------------

    def _complete(self, params):
        uri = params['textDocument']['uri']
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
        if root is None:
            return None
        sym = find_symbol(root, word, pos['line'] + 1)
        if sym is None or sym.kind == 'builtin':
            return None
        return _loc(uri, sym.line - 1)

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
