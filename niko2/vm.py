"""Niko bytecode virtual machine. This executes Niko IR directly."""
import math, os, random, time, datetime, json
from .runtime import NikoRuntimeError, NikoResult, fmt, number
from .ir import FunctionCode

def niko_range(a,b):
    a=int(a); b=int(b)
    return list(range(a,b+1)) if a<=b else list(range(a,b-1,-1))

def _pick(coll):
    items=list(coll)
    if not items: raise NikoRuntimeError("I can't pick from an empty list.")
    return random.choice(items)

def _read_file(name):
    try:
        with open(str(name),encoding='utf-8') as f: return f.read()
    except FileNotFoundError:
        raise NikoRuntimeError(f'I couldn\'t find the file "{name}".')

def _write_file(name,text):
    with open(str(name),'w',encoding='utf-8') as f: f.write(fmt(text))

def _append_file(name,text):
    with open(str(name),'a',encoding='utf-8') as f: f.write(fmt(text))

def _unwrap(r):
    if isinstance(r,NikoResult):
        if r.is_ok: return r.value
        raise NikoRuntimeError(r.message)
    if r is None: raise NikoRuntimeError('tried to unwrap nothing')
    return r

def _unwrap_or(r,d):
    if isinstance(r,NikoResult): return r.value if r.is_ok else d
    return d if r is None else r

def _try_read_file(name):
    try: return NikoResult(True,value=_read_file(name))
    except NikoRuntimeError as e: return NikoResult(False,message=str(e))

def _try_number(x):
    try: return NikoResult(True,value=number(x))
    except NikoRuntimeError as e: return NikoResult(False,message=str(e))

class Frame:
    def __init__(self,code,env=None,name='<main>',constants=None,globals=None):
        self.code=code; self.constants=constants or []; self.env={} if env is None else env; self.stack=[]; self.ip=0; self.name=name; self.iter_stack=[]
        # Alpha 10: the module env, for names that are neither locals nor
        # captured cells (module globals stay one shared namespace).
        self.globals=self.env if globals is None else globals

class Cell:
    """A shared box for a captured variable (Alpha 10 closures).

    Capture is by reference: the defining frame and every closure created
    from it hold the *same* Cell object, so writes are visible everywhere.
    """
    __slots__=('value',)
    def __init__(self,v): self.value=v

class VMFunction:
    def __init__(self,code,cells=None,globals_env=None):
        self.code=code
        self.cells=dict(cells) if cells else {}
        self.globals_env=globals_env
        self._niko_fn_name=code.name
    def __call__(self,*args):
        if len(args)!=len(self.code.params): raise NikoRuntimeError(f'{self.code.name} expected {len(self.code.params)} arguments, got {len(args)}.')
        env=dict(self.cells)
        env.update(zip(self.code.params,args))
        vm=VM(); vm.constants=self.code.constants or []; return vm.execute_code(self.code.code,env,self.code.name,vm.constants,self.globals_env)

class VM:
    def __init__(self): self.output=[]; self.trace_fn=None
    def builtin(self,name):
        b={'text':lambda x:str(x),'number':number,'length':len,'item_of':lambda i,x:x[int(i)-1],
        'upper':lambda x:str(x).upper(),'lower':lambda x:str(x).lower(),'trim':lambda x:str(x).strip(),
        'replace':lambda x,a,c:str(x).replace(str(a),str(c)),'split':lambda x,s=' ':str(x).split(s),
        'join':lambda x,s: str(s).join(map(str,x)),'sorted':lambda x:sorted(x),'reversed':lambda x:list(reversed(x)),
        'unique':lambda x:list(dict.fromkeys(x)),'sum':sum,'average':lambda x:sum(x)/len(x),
        'max':lambda *x:max(x[0]) if len(x)==1 and isinstance(x[0],(list,tuple)) else max(x),
        'min':lambda *x:min(x[0]) if len(x)==1 and isinstance(x[0],(list,tuple)) else min(x),
        'abs':abs,'ceil':math.ceil,'floor':math.floor,'round':round,'sqrt':math.sqrt,'pi':math.pi,
        'random_int':lambda a,b:random.randint(int(a),int(b)),'has':lambda c,x:x in c,
        'today':lambda:datetime.date.today().isoformat(),'now':lambda:datetime.datetime.now().isoformat(),'sleep':time.sleep,'keys':lambda x:list(x.keys()),
        'starts_with':lambda t,p:str(t).startswith(str(p)),'ends_with':lambda t,p:str(t).endswith(str(p)),
        'count_of':lambda c,x:str(c).count(str(x)) if isinstance(c,str) else list(c).count(x),
        'pick':_pick,'write_file':_write_file,'append_file':_append_file,
        'read_file':_read_file,'read_lines':lambda n:_read_file(n).splitlines(),
        'file_exists':lambda n:os.path.exists(str(n)),
        'ok':lambda v:NikoResult(True,value=v),
        'error':lambda m:NikoResult(False,message=fmt(m)),
        'is_ok':lambda r:isinstance(r,NikoResult) and r.is_ok,
        'is_error':lambda r:isinstance(r,NikoResult) and not r.is_ok,
        'unwrap':_unwrap,'unwrap_or':_unwrap_or,
        'error_message':lambda r:r.message if isinstance(r,NikoResult) and not r.is_ok else '',
        'try_read_file':_try_read_file,'try_number':_try_number,
        'niko_range':niko_range}
        return b.get(name)
    def get(self,f,name):
        if name in f.env:
            v=f.env[name]; return v.value if isinstance(v,Cell) else v
        if f.globals is not f.env and name in f.globals:
            v=f.globals[name]; return v.value if isinstance(v,Cell) else v
        b=self.builtin(name)
        if b is not None:return b
        raise NikoRuntimeError(f'I don\'t know what "{name}" is.')
    def execute(self,module,env=None):
        env={} if env is None else env
        # Alpha 10: functions are first-class values. Top-level definitions
        # are installed as values with no captured cells; recursion (and
        # mutual recursion) resolves through the shared globals dict, so the
        # old "live env dict as closure" trick is gone. Nested definitions
        # are installed by MAKE_FUNCTION at runtime, not here.
        for name,fc in module.functions.items():
            if not fc.nested: env[name]=VMFunction(fc,{},env)
        return self.execute_code(module.code,env,constants=module.constants)
    def execute_code(self,code,env,name='<main>',constants=None,globals=None):
        f=Frame(code,env,name,constants if constants is not None else getattr(self,'constants',[]),globals); frames=[f]
        while frames:
            f=frames[-1]
            if f.ip>=len(f.code): frames.pop(); continue
            ins=f.code[f.ip]; f.ip+=1; op=ins.op; a=ins.arg
            if self.trace_fn is not None:
                # Debugger hook (niko2/debug.py). May raise _KillSignal to
                # unwind the loop when the debug session is terminated.
                self.trace_fn(self,frames,f,ins)
            try:
                if op=='HALT': return None
                if op=='PUSH_CONST': f.stack.append(self._const(a, f))
                elif op=='LOAD': f.stack.append(self.get(f,a))
                elif op=='STORE':
                    v=f.stack.pop(); c=f.env.get(a)
                    if isinstance(c,Cell): c.value=v
                    else: f.env[a]=v
                elif op=='POP': f.stack.pop()
                elif op=='SAY':
                    vals=[f.stack.pop() for _ in range(a)][::-1]; print(' '.join(fmt(x) for x in vals))
                elif op=='BUILD_LIST': f.stack.append([f.stack.pop() for _ in range(a)][::-1])
                elif op=='BUILD_RECORD':
                    vals=[f.stack.pop() for _ in range(a*2)][::-1]; f.stack.append({vals[i]:vals[i+1] for i in range(0,len(vals),2)})
                elif op=='UNARY':
                    v=f.stack.pop(); f.stack.append((not bool(v)) if a=='not' else -v)
                elif op=='BINARY':
                    b=f.stack.pop(); x=f.stack.pop(); f.stack.append(self.binary(a,x,b))
                elif op=='INDEX':
                    idx=f.stack.pop(); obj=f.stack.pop(); f.stack.append(obj[int(idx)-1] if isinstance(obj,list) else obj[idx])
                elif op=='STORE_INDEX':
                    val=f.stack.pop(); idx=f.stack.pop(); obj=f.stack.pop()
                    if isinstance(obj,list): obj[int(idx)-1]=val
                    elif isinstance(obj,dict): obj[idx]=val
                    else: raise NikoRuntimeError(f'That position isn\'t in the list (positions start at 1).' if isinstance(idx,(int,float)) else f'Cannot set a key on {type(obj).__name__}.')
                elif op=='LIST_APPEND':
                    val=f.stack.pop(); lst=f.stack.pop()
                    if not isinstance(lst,list): raise NikoRuntimeError('"put ... in" needs a list.')
                    lst.append(val)
                elif op=='LIST_REMOVE':
                    val=f.stack.pop(); lst=f.stack.pop()
                    if not isinstance(lst,list): raise NikoRuntimeError('"remove ... from" needs a list.')
                    if val in lst: lst.remove(val)
                elif op=='IS_LIST':
                    f.stack.append(isinstance(f.stack.pop(),list))
                elif op=='IS_RECORD':
                    f.stack.append(isinstance(f.stack.pop(),dict))
                elif op=='LIST_SLICE':
                    start=f.stack.pop(); obj=f.stack.pop()
                    if not isinstance(obj,list): raise NikoRuntimeError('pattern rest needs a list.')
                    f.stack.append(obj[int(start)-1:])
                elif op=='INPUT':
                    prompt=f.stack.pop()
                    if a:  # ask number: keep asking until a number parses
                        while True:
                            t=input(fmt(prompt)).strip()
                            try: v=int(t)
                            except ValueError:
                                try: v=float(t)
                                except ValueError: print('Please type a number.'); continue
                            f.stack.append(v); break
                    else:
                        f.stack.append(input(fmt(prompt)))
                elif op=='ATTR':
                    obj=f.stack.pop(); f.stack.append(obj[a] if isinstance(obj,dict) else getattr(obj,a))
                elif op=='CALL':
                    args=[f.stack.pop() for _ in range(a)][::-1]; fn=f.stack.pop()
                    if isinstance(fn,VMFunction):
                        if len(args)!=len(fn.code.params): raise NikoRuntimeError(f'{fn.code.name} expected {len(fn.code.params)} arguments, got {len(args)}.')
                        env2=dict(fn.cells); env2.update(zip(fn.code.params,args)); frames.append(Frame(fn.code.code,env2,fn.code.name,fn.code.constants or [],fn.globals_env))
                    elif callable(fn): f.stack.append(fn(*args))
                    else: raise NikoRuntimeError(f"I can't call {fmt(fn)} as a function.")
                elif op=='RETURN':
                    val=f.stack.pop(); frames.pop()
                    if not frames:return val
                    frames[-1].stack.append(val)
                elif op=='JUMP': f.ip=a
                elif op=='JUMP_IF_FALSE':
                    if not bool(f.stack.pop()):f.ip=a
                elif op=='ITER_REPEAT': f.iter_stack.append(iter(range(int(f.stack.pop()))))
                elif op=='REPEAT_NEXT':
                    try: next(f.iter_stack[-1])
                    except StopIteration: f.iter_stack.pop(); f.ip=a
                elif op=='REPEAT_BACK': f.ip=a
                elif op=='ITER_PREP': f.iter_stack.append(iter(f.stack.pop()))
                elif op=='ITER_NEXT':
                    try:f.stack.append(next(f.iter_stack[-1]))
                    except StopIteration:f.iter_stack.pop(); f.ip=a
                elif op=='MAKE_FUNCTION':
                    # Alpha 10: the arg is the function's qualname. Captured
                    # names are wrap-or-created into shared Cells in the
                    # defining frame, so later STOREs in this frame and all
                    # closures see the same box (capture by reference).
                    fc=self._functions[a]; cells={}
                    for cname in fc.captures:
                        c=f.env.get(cname)
                        if not isinstance(c,Cell):
                            c=Cell(c); f.env[cname]=c
                        cells[cname]=c
                    f.stack.append(VMFunction(fc,cells,f.globals))
                else: raise NikoRuntimeError(f'Unknown VM instruction {op}')
            except NikoRuntimeError: raise
            except Exception as e: raise NikoRuntimeError(f'Line {ins.line}: {e}')
        return None
    def run_module(self,module,env=None):
        self._functions=module.functions; return self.execute(module,env)
    def _const(self,i,f):
        # constants are injected as a synthetic attribute by run_compiled
        return f.constants[i]
    def binary(self,op,a,b):
        if op=='+':return a+b
        if op=='-':return a-b
        if op=='*':return a*b
        if op=='/':
            if b==0:raise NikoRuntimeError('You cannot divide by zero.')
            return a/b
        if op=='%':return a%b
        if op=='**':return a**b
        if op=='and':return bool(a) and bool(b)
        if op=='or':return bool(a) or bool(b)
        if op in ('==','is'):return a==b
        if op in ('!=','is not'):return a!=b
        if op in ('<','is smaller than'):return a<b
        if op in ('>','is bigger than'):return a>b
        if op in ('<=','is at most'):return a<=b
        if op in ('>=','is at least'):return a>=b
        if op=='is in':return a in b
        raise NikoRuntimeError(f'Unknown operator {op}')

def run_module(module):
    vm=VM(); vm.constants=module.constants; return vm.run_module(module)
