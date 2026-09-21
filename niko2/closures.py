"""Lexical closure analysis shared by the VM compiler and the WASM/native backends.

Alpha 10: nested ``to`` definitions capture enclosing function locals
*by reference*. This module computes, for every FunctionDef in a checked
program:

- ``captures``: names the function reads from lexically enclosing *function*
  scopes (including pass-through names it only forwards to deeper nests).
  The backend must make these names available as shared boxes when the
  nested ``to`` statement executes.
- ``boxes``: the function's own params/locals that some nested function
  captures. The backend must box these so the box can be shared.
- ``qualname``: a unique module-wide key (``add``, ``outer$inner``,
  ``outer$inner$2``). ``$`` is not a legal Niko identifier character, so
  qualnames can never collide with user-written names.

Module-level (global) names are never captured: the module scope is one
shared namespace on every backend. Builtins and ``pi`` resolve without an
environment and are never captured either.
"""
from dataclasses import dataclass

from .ast import (
    Node, FunctionDef, SetStmt, AskStmt, ForStmt, AugAssignStmt,
    MatchBind, MatchOk, MatchErr, MatchStmt, NameExpr, CallExpr,
    IfStmt, RepeatStmt, WhileStmt,
)


@dataclass
class ClosureInfo:
    node: object            # the FunctionDef node
    qualname: str           # unique module-wide key
    nested: bool            # False for module top-level definitions
    captures: tuple         # names captured from enclosing function scopes
    boxes: tuple            # own params/locals captured by nested functions
    parent: object | None   # enclosing FunctionDef, or None


_BUILTIN_LIKE = None  # filled lazily to avoid a hard import cycle


def _builtin_names():
    global _BUILTIN_LIKE
    if _BUILTIN_LIKE is None:
        from .typecheck import BUILTIN_NAMES
        _BUILTIN_LIKE = set(BUILTIN_NAMES) | {'pi'}
    return _BUILTIN_LIKE


def _stmt_children(stmt):
    """Statement lists nested directly inside stmt (not crossing FunctionDef)."""
    if isinstance(stmt, IfStmt):
        for _, b in stmt.branches:
            yield b
        if stmt.otherwise:
            yield stmt.otherwise
    elif isinstance(stmt, (RepeatStmt, ForStmt, WhileStmt)):
        yield stmt.body
    elif isinstance(stmt, MatchStmt):
        for _, b in stmt.cases:
            yield b
        if stmt.otherwise:
            yield stmt.otherwise


def _walk_stmts(stmts, fn):
    for s in stmts:
        if isinstance(s, FunctionDef):
            continue
        fn(s)
        for child in _stmt_children(s):
            _walk_stmts(child, fn)


def _refs_of_function(node):
    """Ordered (kind, name) references in a function body.

    kind is 'read' or 'write'. A ``set``/``ask``/``for``/match-binding writes
    *after* its value expression is read; params are written first. Nested
    FunctionDef *statements* write their name in the enclosing scope, but
    their bodies are not walked here.
    """
    out = []

    def expr(n):
        if isinstance(n, FunctionDef):
            return
        if isinstance(n, NameExpr):
            out.append(('read', n.name))
            return
        if isinstance(n, Node):
            for f in n.__dataclass_fields__:
                if f == 'line':
                    continue
                v = getattr(n, f)
                if isinstance(v, Node):
                    expr(v)
                elif isinstance(v, (list, tuple)):
                    for x in v:
                        if isinstance(x, Node):
                            expr(x)

    def stmt(s):
        if isinstance(s, FunctionDef):
            out.append(('write', s.name))
            return
        if isinstance(s, SetStmt):
            expr(s.expr)
            out.append(('write', s.name))
            return
        if isinstance(s, AskStmt):
            expr(s.prompt)
            out.append(('write', s.name))
            return
        if isinstance(s, ForStmt):
            expr(s.iterable)
            out.append(('write', s.name))
            for x in s.body:
                stmt(x)
            return
        if isinstance(s, AugAssignStmt):
            out.append(('read', s.name))
            expr(s.expr)
            out.append(('write', s.name))
            return
        if isinstance(s, MatchStmt):
            expr(s.expr)
            for patterns, b in s.cases:
                for p in patterns:
                    if isinstance(p, (MatchBind, MatchOk, MatchErr)):
                        out.append(('write', p.name))
                for x in b:
                    stmt(x)
            if s.otherwise:
                for x in s.otherwise:
                    stmt(x)
            return
        if isinstance(s, IfStmt):
            for c, b in s.branches:
                expr(c)
                for x in b:
                    stmt(x)
            if s.otherwise:
                for x in s.otherwise:
                    stmt(x)
            return
        if isinstance(s, (RepeatStmt, WhileStmt)):
            if isinstance(s, RepeatStmt):
                expr(s.count)
            else:
                expr(s.cond)
            for x in s.body:
                stmt(x)
            return
        # SayStmt, ReturnStmt, ExprStmt, ...: expression reads only
        if isinstance(s, Node):
            for f in s.__dataclass_fields__:
                if f == 'line':
                    continue
                v = getattr(s, f)
                if isinstance(v, Node):
                    expr(v)
                elif isinstance(v, (list, tuple)):
                    for x in v:
                        if isinstance(x, Node):
                            expr(x)

    for p in node.params:
        out.append(('write', p.split(':', 1)[0].strip()))
    for s in node.body:
        stmt(s)
    return out


def _bound_of(node):
    """Names bound by a FunctionDef: params + assignments in its body."""
    bound = set(p.split(':', 1)[0].strip() for p in node.params)

    def visit(s):
        if isinstance(s, SetStmt):
            bound.add(s.name)
        elif isinstance(s, AskStmt):
            bound.add(s.name)
        elif isinstance(s, ForStmt):
            bound.add(s.name)
        elif isinstance(s, AugAssignStmt):
            bound.add(s.name)
        elif isinstance(s, MatchStmt):
            for patterns, _ in s.cases:
                for p in patterns:
                    if isinstance(p, (MatchBind, MatchOk, MatchErr)):
                        bound.add(p.name)
        elif isinstance(s, FunctionDef):
            bound.add(s.name)

    # nested FunctionDef *statements* bind their name in this scope, but their
    # *bodies* belong to the nested scope -- handle them explicitly instead of
    # letting _walk_stmts skip them entirely.
    for s in node.body:
        if isinstance(s, FunctionDef):
            bound.add(s.name)
    _walk_stmts(node.body, visit)
    return bound


def analyze_closures(tree):
    """Map id(FunctionDef) -> ClosureInfo for every function in the program."""
    body = tree.body if isinstance(tree, Node) else tree

    # pass 1: collect functions in pre-order with parent links
    order = []          # FunctionDef nodes, pre-order (parents before children)
    parents = {}        # id(node) -> parent FunctionDef or None

    def collect(stmts, parent):
        for s in stmts:
            if isinstance(s, FunctionDef):
                order.append(s)
                parents[id(s)] = parent
                collect(s.body, s)
            else:
                for child in _stmt_children(s):
                    collect(child, parent)

    collect(body, None)

    bound = {id(n): _bound_of(n) for n in order}
    children = {id(n): [] for n in order}
    for n in order:
        p = parents[id(n)]
        if p is not None:
            children[id(p)].append(n)

    builtins = _builtin_names()

    # external references per function: {name: target FunctionDef}, computed
    # from the ordered reference walk (a name counts when it is not yet bound
    # in this function at that point, so `set n to n + 1` reads the enclosing
    # `n` and then writes through to the same box).
    ext = {}
    for n in order:
        refs = {}
        local = set()
        for kind, x in _refs_of_function(n):
            if x not in local and x not in builtins and x not in refs:
                m = parents[id(n)]
                while m is not None and x not in bound[id(m)]:
                    m = parents[id(m)]
                if m is not None:
                    refs[x] = m
            if kind == 'write':
                local.add(x)
        ext[id(n)] = refs

    # pass 2: captures, children before parents (reverse pre-order), with
    # pass-through: a name captured by a child from *above* the parent must be
    # forwarded through the parent.
    cap_targets = {id(n): {} for n in order}  # id -> {name: target node}
    for n in reversed(order):
        fid = id(n)
        for c in children[fid]:
            for x, t in cap_targets[id(c)].items():
                if t is not n and x not in cap_targets[fid]:
                    cap_targets[fid][x] = t
        for x, t in ext[fid].items():
            if x not in cap_targets[fid]:
                cap_targets[fid][x] = t

    # pass 3: boxes -- names of this function captured from it by descendants
    descendants = {}

    def desc(n):
        fid = id(n)
        if fid not in descendants:
            out = []
            for c in children[fid]:
                out.append(c)
                out.extend(desc(c))
            descendants[fid] = out
        return descendants[fid]

    for n in order:
        desc(n)

    boxes = {}
    for n in order:
        fid = id(n)
        b = set()
        for d in descendants[fid]:
            for x, t in cap_targets[id(d)].items():
                if t is n and x in bound[fid]:
                    b.add(x)
        boxes[fid] = b

    # qualnames, deduped
    used = set()
    qualnames = {}

    def qualname_for(n):
        p = parents[id(n)]
        base = n.name if p is None else qualnames[id(p)] + '$' + n.name
        q = base
        k = 2
        while q in used:
            q = f'{base}${k}'
            k += 1
        used.add(q)
        return q

    for n in order:
        qualnames[id(n)] = qualname_for(n)

    infos = {}
    for n in order:
        fid = id(n)
        infos[fid] = ClosureInfo(
            node=n,
            qualname=qualnames[fid],
            nested=parents[fid] is not None,
            captures=tuple(sorted(cap_targets[fid])),
            boxes=tuple(sorted(boxes[fid])),
            parent=parents[fid],
        )
    return infos
