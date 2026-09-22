from .ast import *


def format_expr(node, parent_prec=0):
    if isinstance(node, LiteralExpr):
        if node.value is None:
            return 'nothing'
        if node.value is True:
            return 'yes'
        if node.value is False:
            return 'no'
        if isinstance(node.value, str):
            return repr(node.value)
        return str(node.value)
    if isinstance(node, NameExpr):
        return node.name
    if isinstance(node, ListExpr):
        return '[' + ', '.join(format_expr(x) for x in node.items) + ']' if node.items else '[]'
    if isinstance(node, RecordExpr):
        if not node.items:
            return '{}'
        return '{' + ', '.join(f'{k}: {format_expr(v)}' for k, v in node.items) + '}'
    if isinstance(node, IndexExpr):
        return f'{format_expr(node.obj)}[{format_expr(node.index)}]'
    if isinstance(node, AttrExpr):
        return f'{format_expr(node.obj)}.{node.name}'
    if isinstance(node, CallExpr):
        args = ', '.join(format_expr(a) for a in node.args)
        return f'{format_expr(node.fn)}({args})'
    if isinstance(node, UnaryExpr):
        inner = format_expr(node.expr, 7)
        return f'{node.op} {inner}' if node.op == 'not' else f'{node.op}{inner}'
    if isinstance(node, BinaryExpr):
        left = format_expr(node.left, 0)
        right = format_expr(node.right, 0)
        return f'{left} {node.op} {right}'
    raise ValueError(f'Unsupported expression node {type(node).__name__}')


def format_match_expr(node, header, indent=0):
    # A match used as an expression: `header` is the first line up to and
    # including `match <subject>:`; arms follow indented like a statement.
    pad = ' ' * indent
    lines = [f'{pad}{header}']
    for case in node.cases:
        pats = ', '.join(format_pattern(p) for p in case.patterns)
        g = f' if {format_expr(case.guard)}' if case.guard is not None else ''
        lines.append(f'{pad}    when {pats}{g}:')
        lines.extend(format_block(case.body, indent + 8))
    if node.otherwise:
        lines.append(f'{pad}    otherwise:')
        lines.extend(format_block(node.otherwise, indent + 8))
    return '\n'.join(lines)

def format_stmt(node, indent=0):
    pad = ' ' * indent
    if isinstance(node, SetStmt):
        ann = f': {node.type_name}' if node.type_name else ''
        if isinstance(node.expr, MatchExpr):
            return format_match_expr(node.expr, f'set {node.name}{ann} to match {format_expr(node.expr.expr)}:', indent)
        return f'{pad}set {node.name}{ann} to {format_expr(node.expr)}'
    if isinstance(node, IndexSetStmt):
        return f'{pad}set {format_expr(node.target)}[{format_expr(node.index)}] to {format_expr(node.expr)}'
    if isinstance(node, AugAssignStmt):
        op = 'add' if node.op == '+' else 'take'
        return f'{pad}{op} {format_expr(node.expr)} to {node.name}' if node.op == '+' else f'{pad}{op} {format_expr(node.expr)} from {node.name}'
    if isinstance(node, PutStmt):
        return f'{pad}put {format_expr(node.value)} in {format_expr(node.target)}'
    if isinstance(node, RemoveStmt):
        return f'{pad}remove {format_expr(node.value)} from {format_expr(node.target)}'
    if isinstance(node, AskStmt):
        kind = 'ask number ' if node.want_number else 'ask '
        return f'{pad}{kind}{format_expr(node.prompt)} into {node.name}'
    if isinstance(node, SayStmt):
        if len(node.exprs) == 1 and isinstance(node.exprs[0], MatchExpr):
            return format_match_expr(node.exprs[0], f'say match {format_expr(node.exprs[0].expr)}:', indent)
        exprs = ', '.join(format_expr(x) for x in node.exprs)
        return f'{pad}say {exprs}' if exprs else f'{pad}say'
    if isinstance(node, AssertStmt):
        s = f'{pad}assert {format_expr(node.cond)}'
        if node.message is not None:
            s += f', {format_expr(node.message)}'
        return s
    if isinstance(node, ExprStmt):
        return f'{pad}{format_expr(node.expr)}'
    if isinstance(node, IfStmt):
        lines = []
        for idx, (cond, body) in enumerate(node.branches):
            prefix = 'if' if idx == 0 else 'otherwise if'
            lines.append(f'{pad}{prefix} {format_expr(cond)}:')
            lines.extend(format_block(body, indent + 4))
        if node.otherwise:
            lines.append(f'{pad}otherwise:')
            lines.extend(format_block(node.otherwise, indent + 4))
        return '\n'.join(lines)
    if isinstance(node, RepeatStmt):
        body = format_block(node.body, indent + 4)
        return f'{pad}repeat {format_expr(node.count)} times:\n' + '\n'.join(body)
    if isinstance(node, ForStmt):
        body = format_block(node.body, indent + 4)
        return f'{pad}for each {node.name} in {format_expr(node.iterable)}:\n' + '\n'.join(body)
    if isinstance(node, WhileStmt):
        body = format_block(node.body, indent + 4)
        return f'{pad}while {format_expr(node.cond)}:\n' + '\n'.join(body)
    if isinstance(node, MatchStmt):
        lines = [f'{pad}match {format_expr(node.expr)}:']
        for case in node.cases:
            pats = ', '.join(format_pattern(p) for p in case.patterns)
            g = f' if {format_expr(case.guard)}' if case.guard is not None else ''
            lines.append(f'{pad}    when {pats}{g}:')
            lines.extend(format_block(case.body, indent + 8))
        if node.otherwise:
            lines.append(f'{pad}    otherwise:')
            lines.extend(format_block(node.otherwise, indent + 8))
        return '\n'.join(lines)
    if isinstance(node, FunctionDef):
        params = ', '.join(
            (f'{p.split(":", 1)[0].strip()}' if ':' not in p else f'{p.split(":", 1)[0].strip()}: {p.split(":", 1)[1].strip()}')
            for p in node.params
        )
        header = f'{pad}to {node.name}'
        if params:
            header += f' with {params}'
        if node.return_type:
            header += f' -> {node.return_type}'
        header += ':'
        body = format_block(node.body, indent + 4)
        return header + '\n' + '\n'.join(body)
    if isinstance(node, ReturnStmt):
        if isinstance(node.expr, MatchExpr):
            return format_match_expr(node.expr, f'give back match {format_expr(node.expr.expr)}:', indent)
        return f'{pad}give back {format_expr(node.expr)}' if node.expr is not None else f'{pad}give back'
    if isinstance(node, UseStmt):
        # Alpha 23: the parser keeps the raw text (quotes included) in
        # `node.module`; strip one surrounding quote pair so the output
        # reads `use "a.niko"`, not `use '"a.niko"'`.
        mod = node.module.strip()
        if len(mod) >= 2 and mod[0] == mod[-1] and mod[0] in '"\'':
            mod = mod[1:-1]
        return f'{pad}use "{mod}"'
    if isinstance(node, ImportStmt):
        return f'{pad}import "{node.path}" as {node.alias}'
    if isinstance(node, StopStmt):
        return f'{pad}stop'
    if isinstance(node, SkipStmt):
        return f'{pad}skip'
    raise ValueError(f'Unsupported statement node {type(node).__name__}')


def format_pattern(p):
    if isinstance(p, MatchLit):
        return format_expr(LiteralExpr(p.line, p.value))
    if isinstance(p, MatchBind):
        return p.name
    if isinstance(p, MatchOk):
        return f'ok {p.name}'
    if isinstance(p, MatchErr):
        return f'error {p.name}'
    if isinstance(p, MatchRest):
        return f'...{p.name}'
    if isinstance(p, MatchList):
        return '[' + ', '.join(format_pattern(i) for i in p.items) + ']'
    if isinstance(p, MatchRecord):
        return '{' + ', '.join(f'{k}: {format_pattern(v)}' for k, v in p.fields) + '}'
    raise ValueError(f'bad pattern {type(p).__name__}')


def format_block(body, indent=0):    return [format_stmt(n, indent) for n in body]


def format_program(tree):
    return '\n'.join(format_block(tree.body)) + '\n'
