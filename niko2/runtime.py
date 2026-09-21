import math, os, random, time, datetime, json

class NikoRuntimeError(Exception): pass
class ReturnSignal(Exception):
    def __init__(self,v): self.value=v
class StopSignal(Exception): pass
class SkipSignal(Exception): pass

class NikoResult:
    """A result<T>: ok(value) or error(message). Alpha 6 error model."""
    __slots__=('is_ok','value','message')
    def __init__(self,is_ok,value=None,message=''):
        self.is_ok=is_ok; self.value=value; self.message=message
    def __eq__(self,other):
        return (isinstance(other,NikoResult) and self.is_ok==other.is_ok
                and self.value==other.value and self.message==other.message)
    def __repr__(self):
        return f'ok({self.value!r})' if self.is_ok else f'error({self.message!r})'

class Env:
    def __init__(self,parent=None): self.data={}; self.parent=parent
    def get(self,k):
        if k in self.data:return self.data[k]
        if self.parent:return self.parent.get(k)
        raise NikoRuntimeError(f'I don\'t know what "{k}" is.')
    def set(self,k,v): self.data[k]=v

class Function:
    def __init__(self,node,closure):
        self.node=node; self.closure=closure
        self._niko_fn_name=node.name
    def __call__(self,*args):
        if len(args)!=len(self.node.params): raise NikoRuntimeError(f'{self.node.name} expected {len(self.node.params)} arguments, got {len(args)}.')
        e=Env(self.closure)
        for p,v in zip(self.node.params,args):
            name=p.split(':',1)[0].strip(); e.set(name,v)
        try: execute(self.node.body,e)
        except ReturnSignal as r:return r.value
        return None

def truth(v): return bool(v)

def _pick_rt(coll):
    items=list(coll)
    if not items: raise NikoRuntimeError("I can't pick from an empty list.")
    return random.choice(items)

def _read_file_rt(name):
    try:
        with open(str(name),encoding='utf-8') as f: return f.read()
    except FileNotFoundError:
        raise NikoRuntimeError(f'I couldn\'t find the file "{name}".')

def _write_file_rt(name,text):
    with open(str(name),'w',encoding='utf-8') as f: f.write(fmt(text))

def _append_file_rt(name,text):
    with open(str(name),'a',encoding='utf-8') as f: f.write(fmt(text))

def _unwrap_rt(r):
    if isinstance(r,NikoResult):
        if r.is_ok: return r.value
        raise NikoRuntimeError(r.message)
    if r is None: raise NikoRuntimeError('tried to unwrap nothing')
    return r

def _unwrap_or_rt(r,d):
    if isinstance(r,NikoResult): return r.value if r.is_ok else d
    return d if r is None else r

def _try_read_file_rt(name):
    try: return NikoResult(True,value=_read_file_rt(name))
    except NikoRuntimeError as e: return NikoResult(False,message=str(e))

def _try_number_rt(x):
    try: return NikoResult(True,value=number(x))
    except NikoRuntimeError as e: return NikoResult(False,message=str(e))

def get_builtin(name):
    b={
      'text':lambda x:str(x), 'number':number, 'length':lambda x:len(x), 'item_of':lambda i,x: x[int(i)-1],
      'upper':lambda x:str(x).upper(),'lower':lambda x:str(x).lower(),'trim':lambda x:str(x).strip(),
      'replace':lambda x,a,b:str(x).replace(str(a),str(b)), 'split':lambda x,s=str(''): str(x).split(s),
      'join':lambda x,s: str(s).join(map(str,x)), 'sorted':lambda x:sorted(x), 'reversed':lambda x:list(reversed(x)),
      'unique':lambda x:list(dict.fromkeys(x)), 'sum':lambda x:sum(x), 'average':lambda x:sum(x)/len(x),
      'max':lambda *x:max(x[0]) if len(x)==1 and isinstance(x[0],(list,tuple)) else max(x),
      'min':lambda *x:min(x[0]) if len(x)==1 and isinstance(x[0],(list,tuple)) else min(x),
      'abs':abs,'ceil':math.ceil,'floor':math.floor,'round':round,'sqrt':math.sqrt,'pi':math.pi,
      'random_int':lambda a,b:random.randint(int(a),int(b)), 'has':lambda c,x:x in c,
      'today':lambda:datetime.date.today().isoformat(),'now':lambda:datetime.datetime.now().isoformat(),
      'sleep':time.sleep,'keys':lambda x:list(x.keys()),
      'starts_with':lambda t,p:str(t).startswith(str(p)),'ends_with':lambda t,p:str(t).endswith(str(p)),
      'count_of':lambda c,x:str(c).count(str(x)) if isinstance(c,str) else list(c).count(x),
      'pick':_pick_rt,'write_file':_write_file_rt,'append_file':_append_file_rt,
      'read_file':_read_file_rt,'read_lines':lambda n:_read_file_rt(n).splitlines(),
      'file_exists':lambda n:os.path.exists(str(n)),
      'ok':lambda v:NikoResult(True,value=v),
      'error':lambda m:NikoResult(False,message=fmt(m)),
      'is_ok':lambda r:isinstance(r,NikoResult) and r.is_ok,
      'is_error':lambda r:isinstance(r,NikoResult) and not r.is_ok,
      'unwrap':_unwrap_rt,'unwrap_or':_unwrap_or_rt,
      'error_message':lambda r:r.message if isinstance(r,NikoResult) and not r.is_ok else '',
      'try_read_file':_try_read_file_rt,'try_number':_try_number_rt,
    }; return b.get(name)

def number(x):
    try:return int(x)
    except: 
      try:return float(x)
      except: raise NikoRuntimeError(f"I can't turn {x!r} into a number.")

def eval_expr(n,e):
    from .ast import NameExpr,LiteralExpr,ListExpr,RecordExpr,CallExpr,IndexExpr,UnaryExpr,BinaryExpr
    if isinstance(n,LiteralExpr): return n.value
    if isinstance(n,NameExpr):
        b=get_builtin(n.name)
        return b if b is not None else e.get(n.name)
    if isinstance(n,ListExpr): return [eval_expr(x,e) for x in n.items]
    if isinstance(n,RecordExpr): return {k:eval_expr(v,e) for k,v in n.items}
    if isinstance(n,CallExpr):
        fn=eval_expr(n.fn,e); return fn(*[eval_expr(a,e) for a in n.args])
    if isinstance(n,IndexExpr):
        obj=eval_expr(n.obj,e); idx=eval_expr(n.index,e); return obj[int(idx)-1] if isinstance(obj,list) else obj[idx]
    if isinstance(n,UnaryExpr):
        v=eval_expr(n.expr,e); return (not truth(v)) if n.op=='not' else -v
    if isinstance(n,BinaryExpr):
        a=eval_expr(n.left,e); b=eval_expr(n.right,e); op=n.op
        if op=='+': return a+b
        if op=='-': return a-b
        if op=='*': return a*b
        if op=='/':
            if b==0: raise NikoRuntimeError('You cannot divide by zero.')
            return a/b
        if op=='%': return a%b
        if op=='**': return a**b
        if op in ('and','or'): return truth(a) and truth(b) if op=='and' else truth(a) or truth(b)
        if op in ('==','is'): return a==b
        if op in ('!=','is not'): return a!=b
        if op in ('<','is smaller than'): return a<b
        if op in ('>','is bigger than'): return a>b
        if op in ('<=','is at most'): return a<=b
        if op in ('>=','is at least'): return a>=b
        if op=='is in': return a in b
    raise NikoRuntimeError('Unsupported expression.')

def execute(body,e):
    from .ast import SetStmt,SayStmt,ExprStmt,ReturnStmt,StopStmt,SkipStmt,FunctionDef,IfStmt,RepeatStmt,ForStmt,WhileStmt,UseStmt
    for n in body:
        try:
            if isinstance(n,SetStmt):
                v=eval_expr(n.expr,e); check_type(n.type_name,v,n.line); e.set(n.name,v)
            elif isinstance(n,SayStmt): print(' '.join(fmt(eval_expr(x,e)) for x in n.exprs))
            elif isinstance(n,ExprStmt): eval_expr(n.expr,e)
            elif isinstance(n,ReturnStmt): raise ReturnSignal(None if n.expr is None else eval_expr(n.expr,e))
            elif isinstance(n,StopStmt): raise StopSignal()
            elif isinstance(n,SkipStmt): raise SkipSignal()
            elif isinstance(n,FunctionDef): e.set(n.name,Function(n,e))
            elif isinstance(n,IfStmt):
                done=False
                for c,b in n.branches:
                    if truth(eval_expr(c,e)): execute(b,e); done=True; break
                if not done and n.otherwise: execute(n.otherwise,e)
            elif isinstance(n,RepeatStmt):
                for _ in range(int(eval_expr(n.count,e))):
                    try: execute(n.body,e)
                    except SkipSignal: continue
                    except StopSignal: break
            elif isinstance(n,ForStmt):
                for v in eval_expr(n.iterable,e):
                    e.set(n.name,v)
                    try: execute(n.body,e)
                    except SkipSignal: continue
                    except StopSignal: break
            elif isinstance(n,WhileStmt):
                guard=0
                while truth(eval_expr(n.cond,e)):
                    guard+=1
                    if guard>10_000_000: raise NikoRuntimeError('Loop ran too long; possible infinite loop.')
                    try: execute(n.body,e)
                    except SkipSignal: continue
                    except StopSignal: break
            elif isinstance(n,UseStmt):
                raise NikoRuntimeError('use is handled by the module loader, not the core runtime.')
        except (ReturnSignal,StopSignal,SkipSignal): raise
        except Exception as ex:
            if isinstance(ex,NikoRuntimeError): raise
            raise NikoRuntimeError(str(ex))

def check_type(t,v,line):
    if not t:return
    base=t.split('<',1)[0]
    ok={'number':(int,float),'text':(str,),'boolean':(bool,),'list':(list,),'map':(dict,),'nothing':(type(None),)}.get(base)
    if ok and not isinstance(v,ok): raise NikoRuntimeError(f'Line {line}: expected {base}, got {type(v).__name__}.')

def fmt(v):
    if isinstance(v,NikoResult):
        return f'ok({fmt(v.value)})' if v.is_ok else f'error("{v.message}")'
    # Alpha 10: first-class functions print as function "name" on every backend.
    _fn=getattr(v,'_niko_fn_name',None)
    if _fn is not None: return f'function "{_fn}"'
    if v is None:return 'nothing'
    if v is True:return 'yes'
    if v is False:return 'no'
    if isinstance(v,float) and v.is_integer():return str(int(v))
    if isinstance(v,str):return v
    if isinstance(v,list):return '['+', '.join(fmt_nested(x) for x in v)+']'
    if isinstance(v,dict):return '{'+', '.join(f'{k}: {fmt_nested(x)}' for k,x in v.items())+'}'
    return str(v)
def fmt_nested(v): return json.dumps(v,ensure_ascii=False) if isinstance(v,str) else fmt(v)
