from dataclasses import dataclass
from .ast import *
from .diagnostics import Diagnostic

class TypeErrorNiko(Diagnostic): pass

@dataclass(frozen=True)
class Type:
    name: str
    arg: 'Type|None'=None
    def __str__(self): return f'{self.name}<{self.arg}>' if self.arg else self.name
ANY=Type('any'); NUMBER=Type('number'); TEXT=Type('text'); BOOLEAN=Type('boolean'); NOTHING=Type('nothing'); LIST=Type('list'); MAP=Type('map'); FUNCTION=Type('function')

def type_from_name(s):
    if not s: return ANY
    s=s.strip();
    if '<' in s and s.endswith('>'):
        base,arg=s[:-1].split('<',1); return Type(base.strip(),type_from_name(arg))
    return Type(s)

def compatible(got,want):
    if want==ANY or got==ANY:
        return True
    if got==want:
        return True
    if want==NUMBER and got==Type('integer'):
        return True
    if got.name == want.name == 'list':
        if want.arg is None:
            return True
        if got.arg is None:
            return True
        return compatible(got.arg, want.arg)
    if got.name == want.name == 'map':
        if want.arg is None:
            return True
        if got.arg is None:
            return True
        return compatible(got.arg, want.arg)
    if got.name == want.name and got.arg is not None and want.arg is not None:
        return compatible(got.arg, want.arg)
    if want.name == 'option':
        # option<T> holds a T or nothing; a plain T is a valid option.
        if got == NOTHING:
            return True
        if got.name == 'option':
            if got.arg is None or want.arg is None:
                return True
            return compatible(got.arg, want.arg)
        if want.arg is None:
            return True
        return compatible(got, want.arg)
    return False


def base_type(t):
    return t.name if isinstance(t, Type) else t

# Every builtin the checker (and the VM) knows. Module-level so IDE
# tooling (completion, hover) stays in sync with the checker by import
# instead of a second hand-maintained list.
BUILTIN_NAMES = {
    'text','number','length','item_of','upper','lower','trim','replace','split','join','sorted','reversed','unique','sum','average','max','min','abs','ceil','floor','round','sqrt','pi','random_int','niko_range','has','today','now','sleep','keys',
    'starts_with','ends_with','count_of','pick','write_file','append_file','read_file','read_lines','file_exists',
    'ok','error','is_ok','is_error','unwrap','unwrap_or','error_message','try_read_file','try_number'
}
# Alpha 10: builtins are not first-class values -- a bare builtin name is only
# legal as the direct target of a call. `pi` is exempt: it is a number value.
BUILTIN_FUNCS = frozenset(BUILTIN_NAMES - {'pi'})

class Checker:
    def __init__(self):
        self.scopes=[{}]; self.functions={}
        self._builtin_names = set(BUILTIN_NAMES)
        # Alpha 10: builtin names the user redefined at module level, so a
        # bare reference resolves to the user's binding, not the builtin.
        self._module_shadowed = set()
        for n in self._builtin_names:
            self.scopes[0][n]=FUNCTION
    def import_names(self,names):
        for n in names:self.scopes[0][n]=ANY
    def suggest_name(self,name):
        candidates = sorted(self._builtin_names | set(self.scopes[0].keys()))
        import difflib
        matches = difflib.get_close_matches(name, candidates, n=1, cutoff=0.45)
        return matches[0] if matches else None
    def error(self,line,msg,token=None): raise TypeErrorNiko(msg,line=line,token=token)
    def define(self,n,t,line):
        if len(self.scopes)==1 and n in BUILTIN_FUNCS:
            self._module_shadowed.add(n)
        self.scopes[-1][n]=t
    def _is_builtin_binding(self,name):
        # True when a bare reference to `name` resolves to the builtin
        # registration (not to a user binding shadowing it).
        for s in reversed(self.scopes):
            if name in s:
                return s is self.scopes[0] and name not in self._module_shadowed
        return False
    def lookup(self,n,line,token=None):
        for s in reversed(self.scopes):
            if n in s:return s[n]
        suggestion = self.suggest_name(n)
        if suggestion:
            self.error(line,f'unknown name "{n}". Did you mean "{suggestion}"?',token=token)
        self.error(line,f'unknown name "{n}"',token=token)
    def check(self,program):
        self.block(program.body, None); return True
    def block(self,body,return_type):
        for n in body:self.stmt(n,return_type)
    def stmt(self,n,return_type):
        if isinstance(n,SetStmt):
            t=self.match_expr(n.expr,return_type) if isinstance(n.expr,MatchExpr) else self.expr(n.expr); want=type_from_name(n.type_name)
            if n.type_name and isinstance(n.expr,ListExpr) and want.name=='list' and want.arg is not None:
                # A list literal is checked against the annotation element by
                # element, so `set xs: list<number> to [1, "a"]` fails instead
                # of collapsing to list<any> and slipping through.
                for x in n.expr.items:
                    et=self.expr(x)
                    if not compatible(et,want.arg):
                        self.error(n.line,f'cannot assign {et} to list<{want.arg}> variable "{n.name}"')
            elif n.type_name and not compatible(t,want):
                self.error(n.line,f'cannot assign {t} to {want} variable "{n.name}"')
            self.define(n.name,want if n.type_name else t,n.line)
        elif isinstance(n,SayStmt):
            for x in n.exprs:
                if isinstance(x,MatchExpr): self.match_expr(x,return_type)
                else: self.expr(x)
        elif isinstance(n,ExprStmt): self.expr(n.expr)
        elif isinstance(n,ReturnStmt):
            got=NOTHING if n.expr is None else (self.match_expr(n.expr,return_type) if isinstance(n.expr,MatchExpr) else self.expr(n.expr))
            if return_type is not None and not compatible(got,return_type): self.error(n.line,f'returns {got}, expected {return_type}')
        elif isinstance(n,FunctionDef):
            ret=type_from_name(n.return_type)
            self.define(n.name,FUNCTION,n.line); self.functions[n.name]=(n,ret)
            self.scopes.append({})
            for p in n.params:
                bits=p.split(':',1); name=bits[0].strip(); pt=type_from_name(bits[1]) if len(bits)>1 else ANY
                self.define(name,pt,n.line)
            self.block(n.body,ret if n.return_type else None); self.scopes.pop()
        elif isinstance(n,IfStmt):
            for c,b in n.branches:
                t=self.expr(c)
                if t not in (BOOLEAN,ANY): self.error(c.line,f'condition must be boolean, got {t}')
                self.scopes.append({}); self.block(b,return_type); self.scopes.pop()
            if n.otherwise:self.scopes.append({});self.block(n.otherwise,return_type);self.scopes.pop()
        elif isinstance(n,(RepeatStmt,WhileStmt)):
            if isinstance(n,RepeatStmt):
                t=self.expr(n.count)
                if t not in (NUMBER,ANY): self.error(n.line,f'repeat count must be number, got {t}')
            else:
                t=self.expr(n.cond)
                if t not in (BOOLEAN,ANY): self.error(n.line,f'while condition must be boolean, got {t}')
            self.scopes.append({});self.block(n.body,return_type);self.scopes.pop()
        elif isinstance(n,ForStmt):
            t=self.expr(n.iterable)
            if base_type(t) not in ('list','map','text') and t not in (ANY,): self.error(n.line,f'cannot iterate over {t}')
            elem=t.arg if (base_type(t)=='list' and isinstance(t,Type) and t.arg is not None) else ANY
            self.scopes.append({}); self.define(n.name,elem,n.line);self.block(n.body,return_type);self.scopes.pop()
        elif isinstance(n,MatchStmt):
            t=self.expr(n.expr)
            for case in n.cases:
                self.scopes.append({})
                for p in case.patterns: self.match_pat(p,t,n.line)
                if case.guard is not None:
                    g=self.expr(case.guard)
                    if g not in (BOOLEAN,ANY): self.error(case.guard.line,f'when guard must be boolean, got {g}')
                self.block(case.body,return_type)
                self.scopes.pop()
            if n.otherwise:
                self.scopes.append({}); self.block(n.otherwise,return_type); self.scopes.pop()
        elif isinstance(n,UseStmt):
            # Alpha 23: `use` merges names into the file's top-level
            # scope through the module pipeline; inside a function body
            # it would be silently ignored by desugaring, so reject it
            # loudly -- same rule as `import` below.
            if len(self.scopes) > 1:
                self.error(n.line, "'use' is only allowed at the top of a file, not inside a function")
        elif isinstance(n,ImportStmt):
            # Alpha 13: `import "…" as alias` binds the alias to the module's
            # export record (typed as a map; attribute access is `any`).
            # Imports only make sense at the top of a file.
            if len(self.scopes) > 1:
                self.error(n.line, 'import must be at the top of the file')
            self.define(n.alias, MAP, n.line)
        elif isinstance(n,IndexSetStmt):
            t=self.expr(n.target)
            if base_type(t) not in ('list','map') and t not in (ANY,): self.error(n.line,f'cannot assign into {t} by index/key')
            self.expr(n.index); self.expr(n.expr)
        elif isinstance(n,AugAssignStmt):
            t=self.lookup(n.name,n.line,token=n.name); v=self.expr(n.expr)
            if t not in (NUMBER,ANY) or v not in (NUMBER,ANY):
                self.error(n.line,f'"{"add" if n.op=="+" else "take"} ... {"to" if n.op=="+" else "from"}" needs numbers')
        elif isinstance(n,PutStmt):
            t=self.expr(n.target)
            if base_type(t) != 'list' and t not in (ANY,): self.error(n.line,f'"put ... in" needs a list, got {t}')
            v=self.expr(n.value)
            if base_type(t)=='list' and isinstance(t,Type) and t.arg is not None and not compatible(v,t.arg):
                self.error(n.line,f'cannot put {v} in list<{t.arg}>')
        elif isinstance(n,RemoveStmt):
            t=self.expr(n.target)
            if base_type(t) != 'list' and t not in (ANY,): self.error(n.line,f'"remove ... from" needs a list, got {t}')
            self.expr(n.value)
        elif isinstance(n,AskStmt):
            self.expr(n.prompt); self.define(n.name,NUMBER if n.want_number else TEXT,n.line)
        elif isinstance(n,MatchStmt):
            t=self.expr(n.expr)
            for case in n.cases:
                self.scopes.append({})
                for p in case.patterns: self.match_pat(p,t,n.line)
                if case.guard is not None:
                    g=self.expr(case.guard)
                    if g not in (BOOLEAN,ANY): self.error(case.guard.line,f'when guard must be boolean, got {g}')
                self.block(case.body,return_type)
                self.scopes.pop()
            if n.otherwise:
                self.scopes.append({}); self.block(n.otherwise,return_type); self.scopes.pop()
    def match_pat(self,p,t,line):
        if isinstance(p,MatchBind):
            self.define(p.name,ANY,line)
        elif isinstance(p,MatchOk):
            if t not in (ANY,) and not (isinstance(t,Type) and t.name=='result'):
                self.error(line,f'cannot match "ok" against {t}')
            elem=t.arg if (isinstance(t,Type) and t.arg is not None) else ANY
            self.define(p.name,elem,line)
        elif isinstance(p,MatchErr):
            if t not in (ANY,) and not (isinstance(t,Type) and t.name=='result'):
                self.error(line,f'cannot match "error" against {t}')
            self.define(p.name,TEXT,line)
        elif isinstance(p,MatchList):
            if t not in (ANY,) and base_type(t)!='list':
                self.error(line,f'cannot match [...] against {t}')
            elem=t.arg if (isinstance(t,Type) and t.name=='list' and t.arg is not None) else ANY
            for item in p.items:
                if isinstance(item,MatchRest):
                    self.define(item.name,Type('list',elem),line)
                else:
                    self.match_pat(item,elem,line)
        elif isinstance(p,MatchRecord):
            if t not in (ANY,) and base_type(t)!='map':
                self.error(line,f'cannot match '+'{...}'+f' against {t}')
            for _,sub in p.fields:
                self.match_pat(sub,ANY,line)
        # MatchLit and MatchRest (handled by MatchList) bind nothing here
    def _match_arm_type(self,body,line,return_type):
        # An arm body's value is its last statement, which must be an expression.
        if not body or not isinstance(body[-1],ExprStmt):
            self.error(line,'match arm must end with an expression to produce a value',token='when')
        for x in body[:-1]: self.stmt(x,return_type)
        return self.expr(body[-1].expr)
    def match_expr(self,n,return_type):
        # A match used as an expression: typed result, exhaustiveness enforced.
        t=self.expr(n.expr)
        if not n.otherwise:
            last=n.cases[-1] if n.cases else None
            exhaustive=(last is not None and last.guard is None and
                        any(isinstance(p,MatchBind) for p in last.patterns))
            if not exhaustive:
                self.error(n.line,'match expression must be exhaustive -- add an otherwise: arm',token='match')
        arm_types=[]
        for case in n.cases:
            self.scopes.append({})
            for p in case.patterns: self.match_pat(p,t,n.line)
            if case.guard is not None:
                g=self.expr(case.guard)
                if g not in (BOOLEAN,ANY): self.error(case.guard.line,f'when guard must be boolean, got {g}')
            arm_types.append(self._match_arm_type(case.body,case.line,return_type))
            self.scopes.pop()
        if n.otherwise:
            self.scopes.append({})
            arm_types.append(self._match_arm_type(n.otherwise,n.line,return_type))
            self.scopes.pop()
        result=arm_types[0]
        for at in arm_types[1:]:
            if not compatible(at,result) and not compatible(result,at):
                self.error(n.line,f'match arms produce different types: {result} vs {at}',token='match')
        if result==ANY:
            for at in arm_types:
                if at!=ANY: result=at; break
        return result
    def expr(self,n):
        if isinstance(n,LiteralExpr):
            if n.value is None:return NOTHING
            if isinstance(n.value,bool):return BOOLEAN
            if isinstance(n.value,(int,float)):return NUMBER
            return TEXT
        if isinstance(n,NameExpr):
            # Alpha 10: builtins are not values. A bare builtin name is only
            # legal as the direct target of a call (handled in the CallExpr
            # branch below, which never reaches this check for that position).
            # A user binding that shadows the builtin is fine.
            if n.name in BUILTIN_FUNCS and self._is_builtin_binding(n.name):
                self.error(n.line,f'can\'t use the builtin "{n.name}" as a value')
            return self.lookup(n.name,n.line,token=n.name)
        if isinstance(n,ListExpr):
            if not n.items: return LIST
            ts=[self.expr(x) for x in n.items]
            if all(t==ts[0] for t in ts[1:]): return Type('list',ts[0])
            # Heterogeneous lists are legal Niko (Niko 1 allows them); they
            # just don't get a precise element type.
            return Type('list',ANY)
        if isinstance(n,RecordExpr):
            for _,v in n.items:self.expr(v)
            return MAP
        if isinstance(n,IndexExpr):
            a=self.expr(n.obj);self.expr(n.index)
            if base_type(a) not in ('list','map','text') and a not in (ANY,):self.error(n.line,f'type {a} cannot be indexed')
            if base_type(a)=='list' and isinstance(a,Type) and a.arg is not None: return a.arg
            return ANY
        if isinstance(n,UnaryExpr):
            t=self.expr(n.expr)
            if n.op=='not':
                if t not in (BOOLEAN,ANY):self.error(n.line,'not requires a boolean')
                return BOOLEAN
            if t not in (NUMBER,ANY):self.error(n.line,'unary - requires a number')
            return NUMBER
        if isinstance(n,BinaryExpr):
            a=self.expr(n.left);b=self.expr(n.right);op=n.op
            if op in ('and','or'):
                if a not in (BOOLEAN,ANY) or b not in (BOOLEAN,ANY):self.error(n.line,f'{op} requires booleans')
                return BOOLEAN
            if op in ('==','!=','is','is not','<','>','<=','>=','is smaller than','is bigger than','is at least','is at most','is in'):return BOOLEAN
            if op=='+':
                if a==TEXT or b==TEXT:return TEXT
                if a in (NUMBER,ANY) and b in (NUMBER,ANY):return NUMBER
                self.error(n.line,'+ requires numbers or text')
            if op in ('-','*','/','%','**'):
                if a not in (NUMBER,ANY) or b not in (NUMBER,ANY):self.error(n.line,f'{op} requires numbers')
                return NUMBER
        if isinstance(n,CallExpr):
            # A builtin name directly in call-target position is always fine;
            # anywhere else (including here, via a variable) a bare builtin is
            # rejected by the NameExpr branch above (Alpha 10).
            if isinstance(n.fn,NameExpr) and n.fn.name in self._builtin_names:
                ft=FUNCTION
            else:
                ft=self.expr(n.fn)
            arg_ts=[self.expr(a) for a in n.args]
            if isinstance(n.fn,NameExpr) and n.fn.name=='item_of' and len(arg_ts)==2:
                # item_of(index, collection) -- the surface syntax is
                # `item N of X`, so the collection is the second argument.
                a1=arg_ts[1]
                if base_type(a1)=='list' and isinstance(a1,Type) and a1.arg is not None: return a1.arg
            return ANY if ft in (FUNCTION,ANY) else self._call_error(n)
        if isinstance(n,AttrExpr): return ANY
        self.error(n.line,'unsupported expression')
    def _call_error(self,n): self.error(n.line,'value is not callable')

def check(program, imported_names=None):
    c=Checker(); c.import_names(imported_names or []); return c.check(program)
