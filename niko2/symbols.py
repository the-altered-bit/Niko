"""Symbol collection for IDE tooling (hover, completion, go-to-definition).

Walks the AST once and records where each name is defined, with an
approximate type string. Deliberately line-oriented: Niko 2 AST nodes
carry line numbers but not columns, so hover and go-to-definition resolve
to the nearest sensible line. Bindings that are really block-local (match
arm bindings, loop variables) are recorded in the enclosing function
scope — close enough for navigation, documented here.
"""
from dataclasses import dataclass, field
from .ast import *


@dataclass
class Symbol:
    name: str
    kind: str            # 'var' | 'func' | 'param' | 'builtin'
    line: int            # 1-based definition line
    type_str: str = 'unknown'
    doc: str = ''


@dataclass
class Scope:
    start: int
    end: int
    symbols: dict = field(default_factory=dict)   # name -> Symbol
    children: list = field(default_factory=list)  # nested Scope


def _literal_type(v):
    if v is None:
        return 'nothing'
    if isinstance(v, bool):
        return 'yes/no'
    if isinstance(v, (int, float)):
        return 'number'
    if isinstance(v, str):
        return 'text'
    if isinstance(v, list):
        return 'list'
    if isinstance(v, dict):
        return 'record'
    return 'unknown'


def _infer(node):
    """Best-effort type string for the right-hand side of a `set`."""
    if isinstance(node, LiteralExpr):
        return _literal_type(node.value)
    if isinstance(node, ListExpr):
        return 'list'
    if isinstance(node, RecordExpr):
        return 'record'
    if isinstance(node, CallExpr) and isinstance(node.fn, NameExpr):
        return f'result of {node.fn.name}(…)'
    return 'unknown'


class _Collector:
    def __init__(self):
        self.root = Scope(1, 10 ** 9)
        self.stack = [self.root]
        self.max_line = 1

    def _scope(self):
        return self.stack[-1]

    def define(self, name, kind, line, type_str='unknown', doc=''):
        if name not in self._scope().symbols:
            self._scope().symbols[name] = Symbol(name, kind, line, type_str, doc)

    def _note(self, node):
        if node.line > self.max_line:
            self.max_line = node.line

    def walk(self, node):
        self._note(node)
        if isinstance(node, Program):
            for x in node.body:
                self.stmt(x)
            self.root.end = self.max_line
        else:
            self.stmt(node)

    def block(self, body):
        for x in body:
            self.stmt(x)

    def stmt(self, n):
        self._note(n)
        if isinstance(n, SetStmt):
            t = n.type_name or _infer(n.expr)
            self.define(n.name, 'var', n.line, t)
            self.walk_expr(n.expr)
        elif isinstance(n, AskStmt):
            self.define(n.name, 'var', n.line, 'number' if n.want_number else 'text')
        elif isinstance(n, ForStmt):
            self.define(n.name, 'var', n.line, 'unknown', 'loop variable')
            self.walk_expr(n.iterable)
            self.block(n.body)
        elif isinstance(n, FunctionDef):
            params = [p.split(':', 1)[0].strip() for p in n.params]
            sig = f'{n.name}({", ".join(params)})' + (f' -> {n.return_type}' if n.return_type else '')
            self.define(n.name, 'func', n.line, n.return_type or 'unknown', sig)
            child = Scope(n.line, 10 ** 9)
            self._scope().children.append(child)
            self.stack.append(child)
            for p in params:
                self.define(p, 'param', n.line)
            self.block(n.body)
            child.end = self.max_line
            self.stack.pop()
        elif isinstance(n, MatchStmt):
            self.walk_expr(n.expr)
            for patterns, body in n.cases:
                for p in patterns:
                    if isinstance(p, (MatchBind, MatchOk, MatchErr)):
                        kind = 'match binding'
                        t = 'unknown' if isinstance(p, MatchBind) else ('text' if isinstance(p, MatchErr) else 'unknown')
                        self.define(p.name, 'var', p.line, t, kind)
                self.block(body)
            if n.otherwise:
                self.block(n.otherwise)
        elif isinstance(n, IfStmt):
            for cond, body in n.branches:
                self.walk_expr(cond)
                self.block(body)
            if n.otherwise:
                self.block(n.otherwise)
        elif isinstance(n, (WhileStmt, RepeatStmt)):
            if isinstance(n, WhileStmt):
                self.walk_expr(n.cond)
            else:
                self.walk_expr(n.count)
            self.block(n.body)
        elif isinstance(n, (PutStmt, RemoveStmt)):
            self.walk_expr(n.value)
            self.walk_expr(n.target)
        elif isinstance(n, SayStmt):
            for x in n.exprs:
                self.walk_expr(x)
        elif isinstance(n, ReturnStmt):
            if n.expr is not None:
                self.walk_expr(n.expr)
        elif isinstance(n, ExprStmt):
            self.walk_expr(n.expr)
        elif isinstance(n, (IndexSetStmt, AugAssignStmt)):
            self.walk_expr(n.expr)

    def walk_expr(self, e):
        if e is None:
            return
        self._note(e)
        for attr in ('left', 'right', 'expr', 'obj', 'index', 'fn'):
            sub = getattr(e, attr, None)
            if sub is not None and hasattr(sub, 'line'):
                self.walk_expr(sub)
        for attr in ('args', 'items'):
            for sub in getattr(e, attr, None) or []:
                if hasattr(sub, 'line'):
                    self.walk_expr(sub)
                elif isinstance(sub, tuple):
                    for s in sub:
                        if hasattr(s, 'line'):
                            self.walk_expr(s)


def collect_symbols(tree):
    """Return the root Scope for a parsed program."""
    c = _Collector()
    c.walk(tree)
    return c.root


def find_symbol(root, name, line):
    """Innermost scope containing `line` that defines `name`, or None."""
    best = None

    def visit(scope):
        nonlocal best
        if scope.start <= line <= scope.end and name in scope.symbols:
            best = scope.symbols[name]
        for ch in scope.children:
            visit(ch)

    visit(root)
    return best


def all_names(root):
    """Every user-defined name in the file (for completion)."""
    out = {}

    def visit(scope):
        for name, sym in scope.symbols.items():
            out.setdefault(name, sym)
        for ch in scope.children:
            visit(ch)

    visit(root)
    return out
