"""Lower Niko AST into stack bytecode. No Python source is generated."""
from .ast import *
from .ir import IRBuilder, FunctionCode, ModuleCode
from .diagnostics import Diagnostic
from .closures import analyze_closures

class CompileError(Diagnostic): pass

class Compiler:
    def compile(self, tree):
        self.b=IRBuilder(); self.loop_stack=[]
        # Alpha 10: every FunctionDef (incl. nested) gets a unique qualname and
        # a captures list; all of them are registered in module_functions so
        # MAKE_FUNCTION can find nested definitions at runtime.
        self.closure_info=analyze_closures(tree)
        self.module_functions={}
        for n in tree.body:
            if isinstance(n,UseStmt): continue
            self.stmt(n)
        self.b.emit('HALT',line=tree.line)
        mod=self.b.finish()
        mod.functions=self.module_functions
        return mod

    def stmt(self,n,b=None):
        b=b or self.b
        if isinstance(n,SetStmt): self.expr(n.expr,b); b.emit('STORE',n.name,n.line)
        elif isinstance(n,IndexSetStmt):
            self.expr(n.target,b); self.expr(n.index,b); self.expr(n.expr,b); b.emit('STORE_INDEX',line=n.line)
        elif isinstance(n,AugAssignStmt):
            b.emit('LOAD',n.name,n.line); self.expr(n.expr,b); b.emit('BINARY',n.op,n.line); b.emit('STORE',n.name,n.line)
        elif isinstance(n,PutStmt):
            self.expr(n.target,b); self.expr(n.value,b); b.emit('LIST_APPEND',line=n.line)
        elif isinstance(n,RemoveStmt):
            self.expr(n.target,b); self.expr(n.value,b); b.emit('LIST_REMOVE',line=n.line)
        elif isinstance(n,AskStmt):
            self.expr(n.prompt,b); b.emit('INPUT',n.want_number,n.line); b.emit('STORE',n.name,n.line)
        elif isinstance(n,SayStmt):
            for x in n.exprs: self.expr(x,b)
            b.emit('SAY',len(n.exprs),n.line)
        elif isinstance(n,ExprStmt): self.expr(n.expr,b); b.emit('POP',line=n.line)
        elif isinstance(n,ReturnStmt):
            if n.expr is None: b.emit('PUSH_CONST',b.const(None,n.line),n.line)
            else: self.expr(n.expr,b)
            b.emit('RETURN',line=n.line)
        elif isinstance(n,FunctionDef):
            info=self.closure_info[id(n)]
            fb=IRBuilder(); old=self.b; oldloops=self.loop_stack
            self.b=fb; self.loop_stack=[]
            for x in n.body: self.stmt(x,fb)
            fb.emit('PUSH_CONST',fb.const(None,n.line),n.line); fb.emit('RETURN',line=n.line)
            self.b=old; self.loop_stack=oldloops
            # Alpha 10: registered module-wide under the qualname so nested
            # definitions survive (the old code dropped the enclosing
            # builder's .functions table, so nested defs KeyError'd at
            # runtime). MAKE_FUNCTION looks the qualname up in VM._functions.
            self.module_functions[info.qualname]=FunctionCode(n.name,[p.split(':',1)[0].strip() for p in n.params],fb.code,n.return_type,fb.constants,
                qualname=info.qualname,captures=info.captures,nested=info.nested)
            self.b.emit('MAKE_FUNCTION',info.qualname,n.line); self.b.emit('STORE',n.name,n.line)
        elif isinstance(n,IfStmt): self.if_stmt(n,b)
        elif isinstance(n,MatchStmt): self.match_stmt(n,b)
        elif isinstance(n,RepeatStmt):
            self.expr(n.count,b); b.emit('ITER_REPEAT',line=n.line)
            start=len(b.code); b.emit('REPEAT_NEXT',None,n.line); end_jump=len(b.code)-1
            self.loop_stack.append((start,[],[]))
            for x in n.body:self.stmt(x,b)
            b.emit('REPEAT_BACK',start,n.line); _,breaks,conts=self.loop_stack.pop()
            end=len(b.code); b.patch(end_jump,end)
            for p in breaks:b.patch(p,end)
            for p in conts:b.patch(p,start)
        elif isinstance(n,ForStmt):
            self.expr(n.iterable,b); b.emit('ITER_PREP',line=n.line); start=len(b.code); nxt=b.emit('ITER_NEXT',None,n.line)
            b.emit('STORE',n.name,n.line)
            self.loop_stack.append((start,[],[]))
            for x in n.body:self.stmt(x,b)
            b.emit('JUMP',start,n.line); _,breaks,conts=self.loop_stack.pop(); end=len(b.code); b.patch(nxt,end)
            for p in breaks:b.patch(p,end)
            for p in conts:b.patch(p,start)
        elif isinstance(n,WhileStmt):
            start=len(b.code); self.expr(n.cond,b); j=b.emit('JUMP_IF_FALSE',None,n.line)
            self.loop_stack.append((start,[],[]))
            for x in n.body:self.stmt(x,b)
            b.emit('JUMP',start,n.line); _,breaks,conts=self.loop_stack.pop(); end=len(b.code); b.patch(j,end)
            for p in breaks:b.patch(p,end)
            for p in conts:b.patch(p,start)
        elif isinstance(n,(StopStmt,SkipStmt)):
            if not self.loop_stack: raise CompileError(f'{"stop" if isinstance(n,StopStmt) else "skip"} must be inside a loop', line=n.line)
            start,breaks,conts=self.loop_stack[-1]; p=b.emit('JUMP',None,n.line)
            (breaks if isinstance(n,StopStmt) else conts).append(p)
        else: raise CompileError(f'unsupported statement {type(n).__name__}', line=n.line)

    def if_stmt(self,n,b):
        exits=[]
        for cond,body in n.branches:
            self.expr(cond,b); jf=b.emit('JUMP_IF_FALSE',None,cond.line)
            for x in body:self.stmt(x,b)
            exits.append(b.emit('JUMP',None,n.line)); b.patch(jf,len(b.code))
        if n.otherwise:
            for x in n.otherwise:self.stmt(x,b)
        end=len(b.code)
        for p in exits:b.patch(p,end)

    def match_stmt(self,n,b):
        # The subject is evaluated once into $match; each pattern is tried in
        # order and the first one that matches (and whose guard, if any,
        # holds) runs its body. Bindings happen before the guard so the
        # guard can reference them; a failed guard falls through to the
        # next pattern exactly like a failed test.
        self.expr(n.expr,b); b.emit('STORE','$match',n.line)
        exits=[]
        for case in n.cases:
            for p in case.patterns:
                fails=[]
                self._test_fails(p,b,'$match',0,fails)
                self.match_bind(p,b,'$match',0)
                if case.guard is not None:
                    self.expr(case.guard,b)
                    fails.append(b.emit('JUMP_IF_FALSE',None,case.guard.line))
                for x in case.body:self.stmt(x,b)
                exits.append(b.emit('JUMP',None,n.line))
                for f_ in fails: b.patch(f_,len(b.code))
        if n.otherwise:
            for x in n.otherwise:self.stmt(x,b)
        end=len(b.code)
        for p in exits:b.patch(p,end)

    def _test_fails(self,p,b,slot,depth,fails):
        # Emits the test for pattern p against the value in hidden slot
        # `slot`. Appends JUMP_IF_FALSE patch points (all patched to the
        # "pattern did not match" label) to `fails`. Nested subject values
        # live in $pat{depth+1} slots -- not valid user identifiers, so they
        # can never be captured or referenced.
        if isinstance(p,MatchLit):
            b.emit('LOAD',slot,p.line)
            b.emit('PUSH_CONST',b.const(p.value,p.line),p.line)
            b.emit('BINARY','==',p.line)
            fails.append(b.emit('JUMP_IF_FALSE',None,p.line))
        elif isinstance(p,MatchBind):
            pass
        elif isinstance(p,MatchOk):
            b.emit('LOAD','is_ok',p.line); b.emit('LOAD',slot,p.line)
            b.emit('CALL',1,p.line)
            fails.append(b.emit('JUMP_IF_FALSE',None,p.line))
        elif isinstance(p,MatchErr):
            b.emit('LOAD','is_error',p.line); b.emit('LOAD',slot,p.line)
            b.emit('CALL',1,p.line)
            fails.append(b.emit('JUMP_IF_FALSE',None,p.line))
        elif isinstance(p,MatchList):
            nfixed=sum(1 for i in p.items if not isinstance(i,MatchRest))
            has_rest=nfixed!=len(p.items)
            b.emit('LOAD',slot,p.line); b.emit('IS_LIST',None,p.line)
            fails.append(b.emit('JUMP_IF_FALSE',None,p.line))
            b.emit('LOAD','length',p.line); b.emit('LOAD',slot,p.line)
            b.emit('CALL',1,p.line)
            b.emit('PUSH_CONST',b.const(nfixed,p.line),p.line)
            b.emit('BINARY','>=' if has_rest else '==',p.line)
            fails.append(b.emit('JUMP_IF_FALSE',None,p.line))
            for i,item in enumerate(p.items):
                if isinstance(item,MatchRest): continue
                sub=f'$pat{depth+1}'
                b.emit('LOAD',slot,p.line)
                b.emit('PUSH_CONST',b.const(i+1,p.line),p.line)
                b.emit('INDEX',line=p.line); b.emit('STORE',sub,p.line)
                self._test_fails(item,b,sub,depth+1,fails)
        elif isinstance(p,MatchRecord):
            b.emit('LOAD',slot,p.line); b.emit('IS_RECORD',None,p.line)
            fails.append(b.emit('JUMP_IF_FALSE',None,p.line))
            for key,subp in p.fields:
                b.emit('LOAD','has',p.line); b.emit('LOAD',slot,p.line)
                b.emit('PUSH_CONST',b.const(key,p.line),p.line)
                b.emit('CALL',2,p.line)
                fails.append(b.emit('JUMP_IF_FALSE',None,p.line))
                sub=f'$pat{depth+1}'
                b.emit('LOAD',slot,p.line)
                b.emit('PUSH_CONST',b.const(key,p.line),p.line)
                b.emit('INDEX',line=p.line); b.emit('STORE',sub,p.line)
                self._test_fails(subp,b,sub,depth+1,fails)
        else: raise CompileError(f'bad pattern {type(p).__name__}', line=p.line)

    def match_bind(self,p,b,slot,depth):
        # Performs the bindings for pattern p, whose subject value is in
        # hidden slot `slot`. Only called after the test passed.
        if isinstance(p,MatchBind):
            b.emit('LOAD',slot,p.line); b.emit('STORE',p.name,p.line)
        elif isinstance(p,MatchOk):
            b.emit('LOAD','unwrap',p.line); b.emit('LOAD',slot,p.line)
            b.emit('CALL',1,p.line); b.emit('STORE',p.name,p.line)
        elif isinstance(p,MatchErr):
            b.emit('LOAD','error_message',p.line); b.emit('LOAD',slot,p.line)
            b.emit('CALL',1,p.line); b.emit('STORE',p.name,p.line)
        elif isinstance(p,MatchLit):
            pass
        elif isinstance(p,MatchList):
            for i,item in enumerate(p.items):
                b.emit('LOAD',slot,p.line)
                b.emit('PUSH_CONST',b.const(i+1,p.line),p.line)
                if isinstance(item,MatchRest):
                    b.emit('LIST_SLICE',None,p.line)
                    b.emit('STORE',item.name,p.line)
                else:
                    sub=f'$pat{depth+1}'
                    b.emit('INDEX',line=p.line); b.emit('STORE',sub,p.line)
                    self.match_bind(item,b,sub,depth+1)
        elif isinstance(p,MatchRecord):
            for key,subp in p.fields:
                sub=f'$pat{depth+1}'
                b.emit('LOAD',slot,p.line)
                b.emit('PUSH_CONST',b.const(key,p.line),p.line)
                b.emit('INDEX',line=p.line); b.emit('STORE',sub,p.line)
                self.match_bind(subp,b,sub,depth+1)
        else: raise CompileError(f'bad pattern {type(p).__name__}', line=p.line)

    def expr(self,n,b):
        if isinstance(n,LiteralExpr): b.emit('PUSH_CONST',b.const(n.value,n.line),n.line)
        elif isinstance(n,NameExpr): b.emit('LOAD',n.name,n.line)
        elif isinstance(n,ListExpr):
            for x in n.items:self.expr(x,b)
            b.emit('BUILD_LIST',len(n.items),n.line)
        elif isinstance(n,RecordExpr):
            for k,v in n.items:
                b.emit('PUSH_CONST',b.const(k,n.line),n.line); self.expr(v,b)
            b.emit('BUILD_RECORD',len(n.items),n.line)
        elif isinstance(n,UnaryExpr): self.expr(n.expr,b); b.emit('UNARY',n.op,n.line)
        elif isinstance(n,BinaryExpr):
            self.expr(n.left,b); self.expr(n.right,b); b.emit('BINARY',n.op,n.line)
        elif isinstance(n,IndexExpr): self.expr(n.obj,b); self.expr(n.index,b); b.emit('INDEX',line=n.line)
        elif isinstance(n,AttrExpr): self.expr(n.obj,b); b.emit('ATTR',n.name,n.line)
        elif isinstance(n,CallExpr):
            self.expr(n.fn,b)
            for x in n.args:self.expr(x,b)
            b.emit('CALL',len(n.args),n.line)
        else: raise CompileError(f'unsupported expression {type(n).__name__}', line=n.line)

def compile_ast(tree): return Compiler().compile(tree)
