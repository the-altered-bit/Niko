"""Niko native backend (Alpha 9): AST -> C -> native executable via cc.

Mirrors the WASM backend's value model and semantics; the C runtime in
niko_runtime.c implements the shared value/builtin behavior.
"""
import dataclasses
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ..ast import (
    Node, Program, SetStmt, IndexSetStmt, AugAssignStmt, PutStmt, RemoveStmt,
    AskStmt, SayStmt, ExprStmt, IfStmt, RepeatStmt, ForStmt, WhileStmt,
    StopStmt, SkipStmt, FunctionDef, ReturnStmt, UseStmt, MatchStmt,
    MatchLit, MatchBind, MatchOk, MatchErr,
    CallExpr, NameExpr, LiteralExpr, ListExpr, RecordExpr, IndexExpr,
    UnaryExpr, BinaryExpr, AttrExpr,
)
from ..compiler import CompileError
from ..typecheck import BUILTIN_NAMES
from . import Backend

_BINOP = {
    '+': 0, '-': 1, '*': 2, '/': 3, '%': 4, '**': 5,
    '==': 6, 'is': 6, '!=': 7, 'is not': 7,
    '<': 8, 'is smaller than': 8, '<=': 9, 'is at most': 9,
    '>': 10, 'is bigger than': 10, '>=': 11, 'is at least': 11,
    'and': 12, 'or': 13, 'is in': 14,
}
_UNOP = {'not': 0, '-': 1}

# niko name -> (argc, C function, special). argc None = no arity check here.
_BUILTIN_C = {
    'text': (1, 'b_text', None),
    'number': (1, 'b_number', None),
    'length': (1, 'b_length', None),
    'item_of': (2, 'b_item_of', None),
    'upper': (1, 'b_upper', None),
    'lower': (1, 'b_lower', None),
    'trim': (1, 'b_trim', None),
    'replace': (3, 'b_replace', None),
    'split': (2, 'b_split', 'split1'),
    'join': (2, 'b_join', None),
    'has': (2, 'b_has', None),
    'starts_with': (2, 'b_starts_with', None),
    'ends_with': (2, 'b_ends_with', None),
    'count_of': (2, 'b_count_of', None),
    'abs': (1, 'b_abs', None),
    'ceil': (1, 'b_ceil', None),
    'floor': (1, 'b_floor', None),
    'round': (1, 'b_round', None),
    'sqrt': (1, 'b_sqrt', None),
    'sum': (1, 'b_sum', None),
    'average': (1, 'b_average', None),
    'max': (None, 'b_max', 'maxmin'),
    'min': (None, 'b_min', 'maxmin'),
    'sorted': (1, 'b_sorted', None),
    'reversed': (1, 'b_reversed', None),
    'unique': (1, 'b_unique', None),
    'keys': (1, 'b_keys', None),
    'niko_range': (2, 'b_niko_range', None),
    'today': (0, 'b_today', None),
    'now': (0, 'b_now', None),
    'sleep': (1, 'b_sleep', None),
    'random_int': (2, 'b_random_int', None),
    'pick': (1, 'b_pick', None),
    'read_file': (1, 'b_read_file', None),
    'write_file': (2, 'b_write_file', None),
    'append_file': (2, 'b_append_file', None),
    'read_lines': (1, 'b_read_lines', None),
    'file_exists': (1, 'b_file_exists', None),
    'try_read_file': (1, 'b_try_read_file', None),
    'ok': (1, 'b_ok', None),
    'error': (1, 'b_error', None),
    'is_ok': (1, 'b_is_ok', None),
    'is_error': (1, 'b_is_error', None),
    'unwrap': (1, 'b_unwrap', None),
    'unwrap_or': (2, 'b_unwrap_or', None),
    'error_message': (1, 'b_error_message', None),
    'try_number': (1, 'b_try_number', None),
}


def _cstr(s):
    """Encode a Python str as a C string literal (UTF-8, octal escapes)."""
    out = ['"']
    for b in s.encode('utf-8'):
        if b == 0x22:
            out.append('\\"')
        elif b == 0x5C:
            out.append('\\\\')
        elif b == 0x0A:
            out.append('\\n')
        elif b == 0x0D:
            out.append('\\r')
        elif b == 0x09:
            out.append('\\t')
        elif 0x20 <= b < 0x7F:
            out.append(chr(b))
        else:
            out.append('\\%03o' % b)
    out.append('"')
    return ''.join(out)


def _cnum(v):
    if isinstance(v, bool):
        raise ValueError('bool is not a number literal')
    if isinstance(v, int):
        return repr(v)
    d = float(v)
    if math.isnan(d):
        return 'NAN'
    if math.isinf(d):
        return 'INFINITY' if d > 0 else '-INFINITY'
    return repr(d)


def _ident(name):
    return ''.join(c if (c.isalnum() or c == '_') else '_' for c in name)


class NikoCCompiler:
    def __init__(self):
        self.lines = []
        self.stmts = None       # current statement list
        self.tmp = 0
        self.fn_counter = 0
        # variable scopes: stack of dicts name -> (kind, c-name);
        # kinds: param, local, global, func. Only function bodies push.
        self.var_scopes = [{}]
        # function visibility for the body currently being generated:
        # niko name -> (c-name, FunctionDef node)
        self.cur_funcs = {}
        self.loop_depth = 0
        # filled by the function-collection pass (keyed by id(node)):
        self.func_cname = {}    # id(FunctionDef) -> C name
        self.func_visible = {}  # id(FunctionDef)/'main' -> visible funcs
        self.functions = []     # (node, cname) in collection order
        self.func_closures = {}  # id(FunctionDef) -> names read from enclosing fns
        self.current_func = None  # id(FunctionDef) while generating its body

    # -- small helpers -------------------------------------------------
    def _emit(self, s):
        self.stmts.append(s)

    def _tmp(self):
        self.tmp += 1
        return f't_{self.tmp}'

    # -- AST shape helpers ---------------------------------------------
    @staticmethod
    def _children(node):
        """Statement children of a node, without crossing FunctionDef."""
        if isinstance(node, IfStmt):
            for _, b in node.branches:
                yield from b
            if node.otherwise:
                yield from node.otherwise
        elif isinstance(node, (RepeatStmt, ForStmt, WhileStmt)):
            yield from node.body
        elif isinstance(node, MatchStmt):
            for _, b in node.cases:
                yield from b
            if node.otherwise:
                yield from node.otherwise

    def _walk(self, stmts, fn):
        for s in stmts:
            if isinstance(s, FunctionDef):
                continue
            fn(s)
            for c in self._children(s):
                self._walk([c], fn)

    def _assigned(self, body):
        """Names bound by set/for/ask/match in a body (not crossing into
        nested function definitions), in first-assignment order."""
        names = []

        def visit(s):
            if isinstance(s, SetStmt):
                names.append(s.name)
            elif isinstance(s, ForStmt):
                names.append(s.name)
            elif isinstance(s, AskStmt):
                names.append(s.name)
            elif isinstance(s, MatchStmt):
                for patterns, _ in s.cases:
                    for p in patterns:
                        if isinstance(p, (MatchBind, MatchOk, MatchErr)):
                            names.append(p.name)

        self._walk(body, visit)
        seen, out = set(), []
        for nm in names:
            if nm not in seen:
                seen.add(nm)
                out.append(nm)
        return out

    # -- function collection pass --------------------------------------
    def _collect_functions(self, body):
        """Assign every FunctionDef a C name; record the functions visible
        from each function body and from main. Nested definitions are
        hoisted to file scope. Also computes, per function, the names it
        reads from lexically enclosing functions (closure reads), which the
        native backend rejects with a clear error.
        """
        scopes = [{}]  # lexical chain of niko-name -> (cname, node)
        fn_stack = []  # enclosing FunctionDef nodes, outermost-first
        self._func_info = {}  # id(node) -> (params set, assigned set)
        self._func_enclosing = {}  # id(node) -> [enclosing nodes]
        self._global_names = set(self._assigned(body))

        def subtree(stmts, out):
            for s in stmts:
                if isinstance(s, FunctionDef):
                    out[s.name] = (self.func_cname[id(s)], s)
                    subtree(s.body, out)
                else:
                    for c in self._children(s):
                        subtree([c], out)

        def walk(stmts):
            for s in stmts:
                if isinstance(s, FunctionDef):
                    self.fn_counter += 1
                    cname = f'niko_fn_{_ident(s.name)}_{self.fn_counter}'
                    scopes[-1][s.name] = (cname, s)
                    self.func_cname[id(s)] = cname
                    self.functions.append((s, cname))
                    params = set(p.split(':', 1)[0].strip()
                                 for p in s.params)
                    assigned = set(self._assigned(s.body))
                    self._func_info[id(s)] = (params, assigned)
                    self._func_enclosing[id(s)] = list(fn_stack)
                    fn_stack.append(s)
                    scopes.append({})
                    walk(s.body)
                    scopes.pop()
                    fn_stack.pop()
                    merged = {}
                    for sc in scopes:
                        merged.update(sc)
                    sub = {}
                    subtree(s.body, sub)
                    merged.update(sub)
                    self.func_visible[id(s)] = merged
                else:
                    for c in self._children(s):
                        walk([c])

        walk(body)
        top = dict(scopes[0])
        sub = {}
        subtree(body, sub)
        top.update(sub)
        self.func_visible['main'] = top
        self._compute_closures()

    def _reads(self, node):
        """Names read by a function body (not crossing nested FunctionDefs).
        Call targets in fn position resolve as functions, not variable reads.
        """
        out = set()

        def visit(n):
            if isinstance(n, FunctionDef):
                return
            if isinstance(n, NameExpr):
                out.add(n.name)
                return
            if isinstance(n, CallExpr):
                if not isinstance(n.fn, NameExpr):
                    visit(n.fn)
                for a in n.args:
                    visit(a)
                return
            if isinstance(n, AugAssignStmt):
                out.add(n.name)
            if dataclasses.is_dataclass(n):
                for f in dataclasses.fields(n):
                    if f.name == 'line':
                        continue
                    v = getattr(n, f.name)
                    if isinstance(v, Node):
                        visit(v)
                    elif isinstance(v, list):
                        for x in v:
                            if isinstance(x, Node):
                                visit(x)

        for st in node.body:
            visit(st)
        return out

    def _compute_closures(self):
        for node, _cname in self.functions:
            fid = id(node)
            params, assigned = self._func_info[fid]
            own = params | assigned
            closures = set()
            for name in self._reads(node):
                if name in own:
                    continue
                if name in self._global_names:
                    continue
                if name in BUILTIN_NAMES or name == 'pi':
                    continue
                if name in self.func_visible.get(fid, {}):
                    continue  # a visible function name, not a variable
                for enc in reversed(self._func_enclosing[fid]):
                    ep, ea = self._func_info[id(enc)]
                    if name in ep or name in ea:
                        closures.add(name)
                        break
            self.func_closures[fid] = closures

    # -- name resolution -----------------------------------------------
    def _resolve(self, name, line):
        for depth, sc in enumerate(reversed(self.var_scopes)):
            if name in sc:
                kind, cname = sc[name]
                if kind == 'func':
                    raise CompileError(
                        f"can't use the function '{name}' as a value yet",
                        line=line)
                if kind in ('local', 'param') and depth > 0:
                    raise CompileError(
                        f"the native backend can't read '{name}' from an "
                        f"outer function yet (closures aren't supported)",
                        line=line)
                return kind, cname
        if (self.current_func is not None
                and name in self.func_closures.get(self.current_func, ())):
            raise CompileError(
                f"the native backend can't read '{name}' from an outer "
                f"function yet (closures aren't supported)", line=line)
        if name in BUILTIN_NAMES:
            raise CompileError(f"can't use the builtin '{name}' as a value",
                               line=line)
        raise CompileError(f'I don\'t know what "{name}" is.', line=line)

    def _store(self, name, expr_c, line):
        sc = self.var_scopes[-1]
        if name in sc:
            kind, cname = sc[name]
            if kind == 'func':
                raise CompileError(f"cannot assign to function '{name}'",
                                   line=line)
            self._emit(f'{cname} = {expr_c};')
            return cname
        if len(self.var_scopes) > 1:
            cname = f'v_{_ident(name)}'
            sc[name] = ('local', cname)
        else:
            cname = f'g_{_ident(name)}'
            sc[name] = ('global', cname)
        self._emit(f'{cname} = {expr_c};')
        return cname

    def _func_lookup(self, name, line):
        try:
            return self.cur_funcs[name]
        except KeyError:
            raise CompileError(f'I don\'t know what "{name}" is.', line=line)

    # -- expressions: each returns a C expression of type NVal* ---------
    def gen_expr(self, n):
        line = n.line
        if isinstance(n, LiteralExpr):
            return self._gen_literal(n.value, line)
        if isinstance(n, NameExpr):
            if n.name == 'pi':
                return 'nval_number(3.141592653589793)'
            _, cname = self._resolve(n.name, line)
            return cname
        if isinstance(n, ListExpr):
            t = self._tmp()
            self._emit(f'NVal *{t} = nval_list();')
            for x in n.items:
                self._emit(f'nval_list_push({t}, {self.gen_expr(x)});')
            return t
        if isinstance(n, RecordExpr):
            t = self._tmp()
            self._emit(f'NVal *{t} = nval_record();')
            for k, v in n.items:
                self._emit(f'nval_record_set({t}, {_cstr(k)}, '
                           f'{len(k.encode("utf-8"))}, {self.gen_expr(v)});')
            return t
        if isinstance(n, IndexExpr):
            o = self.gen_expr(n.obj)
            i = self.gen_expr(n.index)
            return f'nval_index_get({line}, {o}, {i})'
        if isinstance(n, AttrExpr):
            o = self.gen_expr(n.obj)
            return (f'nval_attr({line}, {o}, {_cstr(n.name)}, '
                    f'{len(n.name.encode("utf-8"))})')
        if isinstance(n, UnaryExpr):
            try:
                op = _UNOP[n.op]
            except KeyError:
                raise CompileError(f"unknown operator '{n.op}'", line=line)
            return f'nval_unary({line}, {op}, {self.gen_expr(n.expr)})'
        if isinstance(n, BinaryExpr):
            try:
                op = _BINOP[n.op]
            except KeyError:
                raise CompileError(f"unknown operator '{n.op}'", line=line)
            l = self.gen_expr(n.left)
            r = self.gen_expr(n.right)
            return f'nval_binary({line}, {op}, {l}, {r})'
        if isinstance(n, CallExpr):
            return self._gen_call(n)
        raise CompileError(f"the native backend can't compile "
                           f"{type(n).__name__} yet", line=line)

    def _gen_literal(self, v, line):
        if v is None:
            return 'nval_nothing()'
        if v is True:
            return 'nval_yes()'
        if v is False:
            return 'nval_no()'
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return f'nval_number({_cnum(v)})'
        if isinstance(v, str):
            return f'nval_text({_cstr(v)}, {len(v.encode("utf-8"))})'
        raise CompileError("the native backend can't compile that literal "
                           "yet", line=line)

    def _gen_call(self, n):
        line = n.line
        fn = n.fn
        if isinstance(fn, NameExpr):
            name = fn.name
            if name == 'pi':
                raise CompileError("'pi' is a value, not a function",
                                   line=line)
            if name in _BUILTIN_C:
                return self._gen_builtin_call(name, n.args, line)
            if name in BUILTIN_NAMES:
                raise CompileError(
                    f"the native backend doesn't support '{name}' yet",
                    line=line)
            cname, fnode = self._func_lookup(name, line)
            params = [p.split(':', 1)[0].strip() for p in fnode.params]
            if len(n.args) != len(params):
                raise CompileError(
                    f"'{name}' takes {len(params)} argument(s) "
                    f"({len(n.args)} given)", line=line)
            args = ', '.join(self.gen_expr(a) for a in n.args)
            return f'{cname}({args})'
        if isinstance(fn, AttrExpr):
            raise CompileError(
                "the native backend doesn't support method calls yet",
                line=line)
        raise CompileError("the native backend can't call that yet", line=line)

    def _gen_builtin_call(self, name, args, line):
        argc, cname, special = _BUILTIN_C[name]
        if special == 'maxmin':
            t = self._tmp()
            self._emit(f'NVal *{t} = nval_list();')
            for a in args:
                self._emit(f'nval_list_push({t}, {self.gen_expr(a)});')
            return f'{cname}({line}, {t})'
        if special == 'split1':
            if len(args) == 1:
                return (f'{cname}({line}, {self.gen_expr(args[0])}, '
                        f'nval_text(" ", 1))')
            if len(args) != 2:
                raise CompileError(
                    f"'{name}' takes 1 or 2 arguments ({len(args)} given)",
                    line=line)
            # fall through to the normal 2-arg call below
        if argc is not None and len(args) != argc:
            raise CompileError(
                f"'{name}' takes {argc} argument(s) ({len(args)} given)",
                line=line)
        argcs = ', '.join(self.gen_expr(a) for a in args)
        sep = ', ' if argcs else ''
        return f'{cname}({line}{sep}{argcs})'

    # -- statements ----------------------------------------------------
    def gen_stmt(self, n):
        line = n.line
        if isinstance(n, SetStmt):
            self._store(n.name, self.gen_expr(n.expr), line)
        elif isinstance(n, IndexSetStmt):
            o = self.gen_expr(n.target)
            i = self.gen_expr(n.index)
            v = self.gen_expr(n.expr)
            self._emit(f'nval_index_set({line}, {o}, {i}, {v});')
        elif isinstance(n, AugAssignStmt):
            try:
                op = _BINOP[n.op]
            except KeyError:
                raise CompileError(f"unknown operator '{n.op}'", line=line)
            _, cname = self._resolve(n.name, line)
            v = self.gen_expr(n.expr)
            self._emit(f'{cname} = nval_binary({line}, {op}, {cname}, {v});')
        elif isinstance(n, PutStmt):
            v = self.gen_expr(n.value)
            t = self.gen_expr(n.target)
            self._emit(f'nval_put_in({line}, {t}, {v});')
        elif isinstance(n, RemoveStmt):
            t = self.gen_expr(n.target)
            v = self.gen_expr(n.value)
            self._emit(f'nval_list_remove({line}, {t}, {v});')
        elif isinstance(n, AskStmt):
            cname = self._store(n.name,
                                f'niko_ask_val({self.gen_expr(n.prompt)}, '
                                f'{1 if n.want_number else 0})', line)
            # _store already emitted; nothing more to do
            _ = cname
        elif isinstance(n, SayStmt):
            parts = [self.gen_expr(e) for e in n.exprs]
            if parts:
                t = self._tmp()
                self._emit(f'NVal *{t}[] = {{{", ".join(parts)}}};')
                self._emit(f'niko_say({len(parts)}, {t});')
            else:
                self._emit(f'niko_say(0, (NVal**)0);')
        elif isinstance(n, ExprStmt):
            self._emit(f'(void){self.gen_expr(n.expr)};')
        elif isinstance(n, IfStmt):
            first = True
            for cond, body in n.branches:
                kw = 'if' if first else 'else if'
                first = False
                c = self.gen_expr(cond)
                self._emit(f'{kw} (nval_truthy({c})) {{')
                for s in body:
                    self.gen_stmt(s)
                self._emit('}')
            if n.otherwise:
                self._emit('else {')
                for s in n.otherwise:
                    self.gen_stmt(s)
                self._emit('}')
        elif isinstance(n, RepeatStmt):
            c = self.gen_expr(n.count)
            tc, ti = self._tmp(), self._tmp()
            self._emit(f'{{ int64_t {tc} = nval_to_int({line}, {c});')
            self._emit(f'for (int64_t {ti} = 0; {ti} < {tc}; {ti}++) {{')
            self.loop_depth += 1
            for s in n.body:
                self.gen_stmt(s)
            self.loop_depth -= 1
            self._emit('} }')
        elif isinstance(n, ForStmt):
            it = self.gen_expr(n.iterable)
            tt, tk = self._tmp(), self._tmp()
            self._emit('{')
            self._emit(f'NVal *{tt} = nval_to_iter_list({line}, {it});')
            self._emit(f'for (int64_t {tk} = 0; {tk} < nval_list_len({tt}); '
                       f'{tk}++) {{')
            cname = self._store(n.name,
                                f'nval_list_item({tt}, {tk})', line)
            _ = cname
            self.loop_depth += 1
            for s in n.body:
                self.gen_stmt(s)
            self.loop_depth -= 1
            self._emit('} }')
        elif isinstance(n, WhileStmt):
            c = self.gen_expr(n.cond)
            self._emit(f'while (nval_truthy({c})) {{')
            self.loop_depth += 1
            for s in n.body:
                self.gen_stmt(s)
            self.loop_depth -= 1
            self._emit('}')
        elif isinstance(n, StopStmt):
            if self.loop_depth == 0:
                raise CompileError("'stop' needs a loop", line=line)
            self._emit('break;')
        elif isinstance(n, SkipStmt):
            if self.loop_depth == 0:
                raise CompileError("'skip' needs a loop", line=line)
            self._emit('continue;')
        elif isinstance(n, FunctionDef):
            pass  # hoisted: emitted at file scope
        elif isinstance(n, ReturnStmt):
            if n.expr is not None:
                self._emit(f'return {self.gen_expr(n.expr)};')
            else:
                self._emit('return nval_nothing();')
        elif isinstance(n, UseStmt):
            raise CompileError("the native backend doesn't support 'use' yet",
                               line=line)
        elif isinstance(n, MatchStmt):
            self._gen_match(n)
        else:
            raise CompileError(f"the native backend can't compile "
                               f"{type(n).__name__} yet", line=line)

    def _gen_match(self, n):
        line = n.line
        ts = self._tmp()
        self._emit(f'NVal *{ts} = {self.gen_expr(n.expr)};')
        exits = []
        for patterns, body in n.cases:
            first = True
            for p in patterns:
                kw = 'if' if first else 'else if'
                first = False
                self._emit(f'{kw} ({self._match_test(p, ts, p.line)}) {{')
                self._match_bind(p, ts, p.line)
                for s in body:
                    self.gen_stmt(s)
                te = self._tmp()
                self._emit(f'goto match_end_{te};')
                exits.append(te)
                self._emit('}')
        if n.otherwise:
            self._emit('{')
            for s in n.otherwise:
                self.gen_stmt(s)
            self._emit('}')
        for te in exits:
            self._emit(f'match_end_{te}: ;')
        _ = line

    def _match_test(self, p, ts, line):
        if isinstance(p, MatchLit):
            lit = self._gen_literal(p.value, line)
            return f'nval_truthy(nval_binary({line}, 6, {ts}, {lit}))'
        if isinstance(p, MatchBind):
            return '1'
        if isinstance(p, MatchOk):
            return f'nval_truthy(b_is_ok({line}, {ts}))'
        if isinstance(p, MatchErr):
            return f'nval_truthy(b_is_error({line}, {ts}))'
        raise CompileError(f'bad pattern {type(p).__name__}', line=line)

    def _match_bind(self, p, ts, line):
        if isinstance(p, MatchBind):
            self._store(p.name, ts, line)
        elif isinstance(p, MatchOk):
            self._store(p.name, f'b_unwrap({line}, {ts})', line)
        elif isinstance(p, MatchErr):
            self._store(p.name, f'b_error_message({line}, {ts})', line)

    # -- functions / main ----------------------------------------------
    def _gen_function_body(self, node):
        cname = self.func_cname[id(node)]
        params = [p.split(':', 1)[0].strip() for p in node.params]
        pc = ', '.join(f'NVal *p_{_ident(p)}' for p in params)
        self.lines.append(f'static NVal *{cname}({pc}) {{')
        self.var_scopes.append({})
        for p in params:
            self.var_scopes[-1][p] = ('param', f'p_{_ident(p)}')
        for nm in self._assigned(node.body):
            self.var_scopes[-1][nm] = ('local', f'v_{_ident(nm)}')
            self.lines.append(f'NVal *v_{_ident(nm)} = nval_nothing();')
        self.cur_funcs = self.func_visible[id(node)]
        self.current_func = id(node)
        saved, self.stmts = self.stmts, []
        for s in node.body:
            self.gen_stmt(s)
        self.lines.extend(self.stmts)
        self.lines.append('return nval_nothing();')
        self.lines.append('}')
        self.stmts = saved
        self.var_scopes.pop()
        self.current_func = None

    def compile_c(self, tree):
        body = tree.body if isinstance(tree, Program) else tree
        self._collect_functions(body)
        out = ['#include "niko_runtime.h"', '']
        gnames = self._assigned(body)
        for nm in gnames:
            out.append(f'static NVal *g_{_ident(nm)};')
            self.var_scopes[0][nm] = ('global', f'g_{_ident(nm)}')
        if out[-1] != '':
            out.append('')
        for node, cname in self.functions:
            params = [p.split(':', 1)[0].strip() for p in node.params]
            pc = ', '.join(f'NVal *p_{_ident(p)}' for p in params)
            out.append(f'static NVal *{cname}({pc});')
        out.append('')
        self.lines = out
        for node, _cname in self.functions:
            self._gen_function_body(node)
        # main runs in the global scope (var_scopes[0])
        self.lines.append('int main(void) {')
        for nm in gnames:
            self.lines.append(f'g_{_ident(nm)} = nval_nothing();')
        self.cur_funcs = self.func_visible['main']
        saved, self.stmts = self.stmts, []
        for s in body:
            self.gen_stmt(s)
        self.lines.extend(self.stmts)
        self.lines.append('return 0;')
        self.lines.append('}')
        self.stmts = saved
        return '\n'.join(self.lines) + '\n'


class NativeBackend(Backend):
    """Compile Niko to C, then to a native executable with the system C
    compiler. compile() returns the executable's bytes."""

    def compile_c(self, tree):
        return NikoCCompiler().compile_c(tree)

    def compile(self, tree) -> bytes:
        src = self.compile_c(tree)
        cc = (shutil.which('cc') or shutil.which('gcc')
              or shutil.which('clang'))
        if not cc:
            raise CompileError(
                'the native backend needs a C compiler on PATH '
                '(cc, gcc, or clang)')
        rt = Path(__file__).parent
        with tempfile.TemporaryDirectory() as tmp:
            c_path = Path(tmp) / 'prog.c'
            c_path.write_text(src, encoding='utf-8')
            exe = Path(tmp) / 'prog'
            r = subprocess.run(
                [cc, '-O2', '-I', str(rt), '-o', str(exe), str(c_path),
                 str(rt / 'niko_runtime.c'), '-lm'],
                capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                raise CompileError(
                    'the C compiler failed:\n' + r.stderr[:2000])
            return exe.read_bytes()

    def compile_file(self, tree, out_path):
        data = self.compile(tree)
        p = Path(out_path)
        p.write_bytes(data)
        try:
            p.chmod(0o755)
        except OSError:
            pass
        return p

    @property
    def output_extension(self):
        return '.exe' if sys.platform == 'win32' else ''
