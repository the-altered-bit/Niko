from .lexer import lex_expr, Token, LexError
from .ast import *
from .diagnostics import Diagnostic, format_diagnostic

class ParseError(Diagnostic):
    def __init__(self, message, line=None, col=None, end_col=None, token=None):
        super().__init__(message, line=line, col=col, end_col=end_col, token=token)


def format_parse_error(source, exc, path='<memory>'):
    """Render a parse error with source line and caret. Kept under this
    name because cli.py imports it; delegates to diagnostics."""
    return format_diagnostic(source, exc, path)


def _parse_import(text, line_no, lines, i, ind):
    # Alpha 13: `import "path/to/file.niko" as alias` -- top-level only.
    rest = text[len('import '):].strip()
    base_col = ind + 1 + len('import ')
    if not rest or rest[0] not in '"\'':
        raise ParseError('expected a quoted file path after import, e.g. import "math.niko" as m',
                         line_no, col=base_col)
    quote = rest[0]
    end = rest.find(quote, 1)
    if end == -1:
        raise ParseError('unterminated file path in import', line_no, col=base_col)
    path = rest[1:end]
    after = rest[end + 1:].strip()
    if not (after.startswith('as ') or after.startswith('as\t')):
        raise ParseError('expected as ALIAS after the import path, e.g. import "math.niko" as m',
                         line_no, col=base_col + end + 1)
    alias = after[2:].strip()
    if not __import__('re').match(r'^[A-Za-z_]\w*$', alias):
        raw_after = rest[end + 1:]
        alias_col = base_col + (end + 1) + (len(raw_after) - len(after)) + len('as ')
        raise ParseError('import alias must be a plain name', line_no, col=alias_col)
    return ImportStmt(line_no, path, alias), i + 1


def _kw_col(lines, i, ind, keyword):
    """1-based column of `keyword` in the raw source line, else statement start."""
    k = lines[i].find(keyword, ind)
    return k + 1 if k != -1 else ind + 1

BLOCK_STARTERS=('if ','repeat ','for each ','while ','to ','match ')

def indent_of(s): return len(s)-len(s.lstrip(' '))

def parse(source):
    lines=source.splitlines(); items=[]; i=0
    while i<len(lines):
        raw=lines[i]
        if not raw.strip() or raw.lstrip().startswith('#'): i+=1; continue
        ind=indent_of(raw)
        if ind: raise ParseError('unexpected indentation', i+1, col=ind+1)
        node,i=parse_stmt(lines,i,ind)
        items.append(node)
    return Program(1,items)

def require_colon(raw,line):
    s=raw.rstrip()
    if not s.endswith(':'): raise ParseError('block statement needs ":"', line, col=len(s)+1)

def child_block(lines,start,parent_indent):
    i=start; body=[]
    while i<len(lines):
        if not lines[i].strip() or lines[i].lstrip().startswith('#'): i+=1; continue
        ind=indent_of(lines[i])
        if ind<=parent_indent: break
        node,i=parse_stmt(lines,i,ind); body.append(node)
    if not body:
        if start < len(lines):
            bad_col = indent_of(lines[start]) + 1
        else:
            bad_col = None
        raise ParseError('expected an indented block', start+1, col=bad_col)
    return body,i

def split_top(s, sep=','):
    out=[]; cur=''; depth=0; quote=None
    for c in s:
        if quote:
            cur+=c
            if c==quote: quote=None
        elif c in "'\"": quote=c; cur+=c
        elif c in '([{': depth+=1; cur+=c
        elif c in ')]}': depth-=1; cur+=c
        elif c==sep and depth==0: out.append(cur.strip()); cur=''
        else: cur+=c
    if cur.strip() or s.strip()==sep: out.append(cur.strip())
    return out

def find_top_level(s, needle):
    """Index of the first occurrence of `needle` (e.g. ' to ', ' in ', ' from ')
    outside quotes and brackets, or -1 if none. Used to split statement forms
    like `put VALUE in LIST` where VALUE/LIST are themselves free expressions
    and may not be split on the first naive substring match."""
    depth=0; quote=None; i=0; n=len(needle)
    while i<len(s):
        c=s[i]
        if quote:
            if c==quote: quote=None
            i+=1; continue
        if c in "'\"": quote=c; i+=1; continue
        if c in '([{': depth+=1; i+=1; continue
        if c in ')]}': depth-=1; i+=1; continue
        if depth==0 and s[i:i+n]==needle: return i
        i+=1
    return -1

def _match_value_at(lines,i,ind,rest,line_no):
    # If `rest` is a whole `match EXPR:` value, parse the match expression and
    # its arms. Returns (MatchExpr, j) or None.
    v=rest.strip()
    if v.startswith('match ') and v.endswith(':'):
        return parse_match_value(lines,i,ind,v,line_no)
    return None

def parse_stmt(lines,i,ind):
    line_no=i+1; text=lines[i].strip()
    if text.startswith('set '):
        rest=text[4:]
        m=__import__('re').match(r'([A-Za-z_]\w*)(?::\s*([A-Za-z_]\w*(?:<[^<>]*(?:<[^<>]*>[^<>]*)*>)?))?\s+to\s+(.+)$',rest)
        if m:
            mv=_match_value_at(lines,i,ind,m.group(3),line_no)
            if mv is not None: mexpr,j=mv; return SetStmt(line_no,m.group(1),mexpr,m.group(2)),j
            return SetStmt(line_no,m.group(1),parse_expr(m.group(3),line_no),m.group(2)),i+1
        # Not a plain `set NAME[: type] to VALUE` -- try an indexed/keyed
        # target: `set item N of LIST to VALUE` or `set NAME[KEY] to VALUE`.
        pos=find_top_level(rest,' to ')
        if pos==-1: raise ParseError('expected set NAME [type] to VALUE', line_no, col=_kw_col(lines,i,ind,'set '))
        target_str=rest[:pos].strip(); value_str=rest[pos+4:].strip()
        if target_str.startswith('item '): target_str=target_str[5:].strip()
        target_expr=parse_expr(target_str,line_no); value_expr=parse_expr(value_str,line_no)
        if isinstance(target_expr,CallExpr) and isinstance(target_expr.fn,NameExpr) and target_expr.fn.name=='item_of' and len(target_expr.args)==2:
            return IndexSetStmt(line_no,target_expr.args[1],target_expr.args[0],value_expr),i+1
        if isinstance(target_expr,IndexExpr):
            return IndexSetStmt(line_no,target_expr.obj,target_expr.index,value_expr),i+1
        raise ParseError('cannot assign to this target', line_no, col=ind+1+4+max(0,rest.find(target_str)))
    if text.startswith('put '):
        rest=text[4:]; pos=find_top_level(rest,' in ')
        if pos==-1: raise ParseError('expected put VALUE in LIST', line_no, col=_kw_col(lines,i,ind,'put '))
        return PutStmt(line_no,parse_expr(rest[:pos].strip(),line_no),parse_expr(rest[pos+4:].strip(),line_no)),i+1
    if text.startswith('remove '):
        rest=text[7:]; pos=find_top_level(rest,' from ')
        if pos==-1: raise ParseError('expected remove VALUE from LIST', line_no, col=_kw_col(lines,i,ind,'remove '))
        return RemoveStmt(line_no,parse_expr(rest[:pos].strip(),line_no),parse_expr(rest[pos+6:].strip(),line_no)),i+1
    if text.startswith('add '):
        rest=text[4:]; pos=find_top_level(rest,' to ')
        if pos==-1: raise ParseError('expected add VALUE to NAME', line_no, col=_kw_col(lines,i,ind,'add '))
        name=rest[pos+4:].strip()
        if not __import__('re').match(r'^[A-Za-z_]\w*$',name):
            seg=rest[pos+4:]; lead=len(seg)-len(seg.lstrip())
            raise ParseError('"add ... to" needs a plain variable name', line_no, col=ind+1+4+pos+4+lead)
        return AugAssignStmt(line_no,name,'+',parse_expr(rest[:pos].strip(),line_no)),i+1
    if text.startswith('take '):
        rest=text[5:]; pos=find_top_level(rest,' from ')
        if pos==-1: raise ParseError('expected take VALUE from NAME', line_no, col=_kw_col(lines,i,ind,'take '))
        name=rest[pos+6:].strip()
        if not __import__('re').match(r'^[A-Za-z_]\w*$',name):
            seg=rest[pos+6:]; lead=len(seg)-len(seg.lstrip())
            raise ParseError('"take ... from" needs a plain variable name', line_no, col=ind+1+5+pos+6+lead)
        return AugAssignStmt(line_no,name,'-',parse_expr(rest[:pos].strip(),line_no)),i+1
    if text.startswith('say') and (len(text)==3 or text[3].isspace()):
        rest=text[3:].strip()
        mv=_match_value_at(lines,i,ind,rest,line_no)
        if mv is not None: mexpr,j=mv; return SayStmt(line_no,[mexpr]),j
        return SayStmt(line_no, [] if not rest else [parse_expr(x,line_no) for x in split_top(rest)]),i+1
    if text=='stop': return StopStmt(line_no),i+1
    if text=='skip': return SkipStmt(line_no),i+1
    if text.startswith('give back'):
        rest=text[9:].strip()
        mv=_match_value_at(lines,i,ind,rest,line_no)
        if mv is not None: mexpr,j=mv; return ReturnStmt(line_no,mexpr),j
        return ReturnStmt(line_no,None if not rest else parse_expr(rest,line_no)),i+1
    if text.startswith('import '): return _parse_import(text, line_no, lines, i, ind)
    if text.startswith('use '): return UseStmt(line_no,text[4:].strip()),i+1
    if text.startswith('ask number '):
        rest=text[11:]; pos=find_top_level(rest,' into ')
        if pos==-1: raise ParseError('expected ask number PROMPT into NAME', line_no, col=_kw_col(lines,i,ind,'ask number '))
        name=rest[pos+6:].strip()
        if not __import__('re').match(r'^[A-Za-z_]\w*$',name):
            seg=rest[pos+6:]; lead=len(seg)-len(seg.lstrip())
            raise ParseError('"ask ... into" needs a plain variable name', line_no, col=ind+1+11+pos+6+lead)
        return AskStmt(line_no,name,parse_expr(rest[:pos].strip(),line_no),True),i+1
    if text.startswith('ask '):
        rest=text[4:]; pos=find_top_level(rest,' into ')
        if pos==-1: raise ParseError('expected ask PROMPT into NAME', line_no, col=_kw_col(lines,i,ind,'ask '))
        name=rest[pos+6:].strip()
        if not __import__('re').match(r'^[A-Za-z_]\w*$',name):
            seg=rest[pos+6:]; lead=len(seg)-len(seg.lstrip())
            raise ParseError('"ask ... into" needs a plain variable name', line_no, col=ind+1+4+pos+6+lead)
        return AskStmt(line_no,name,parse_expr(rest[:pos].strip(),line_no),False),i+1
    if text.startswith('if '): return parse_if(lines,i,ind)
    if text.startswith('repeat '):
        require_colon(lines[i],line_no); count=text[7:-1].strip()
        body,j=child_block(lines,i+1,ind); return RepeatStmt(line_no,parse_expr(count[:-6].strip(),line_no) if count.endswith(' times') else parse_expr(count,line_no),body),j
    if text.startswith('for each '):
        require_colon(lines[i],line_no); rest=text[9:-1].strip();
        m=__import__('re').match(r'([A-Za-z_]\w*)\s+in\s+(.+)$',rest)
        if not m: raise ParseError('expected for each NAME in VALUE:', line_no, col=_kw_col(lines,i,ind,'for each '))
        body,j=child_block(lines,i+1,ind); return ForStmt(line_no,m.group(1),parse_expr(m.group(2),line_no),body),j
    if text.startswith('while '):
        require_colon(lines[i],line_no); body,j=child_block(lines,i+1,ind); return WhileStmt(line_no,parse_expr(text[6:-1].strip(),line_no),body),j
    if text.startswith('to '):
        require_colon(lines[i],line_no); head=text[3:-1].strip(); ret=None
        if ' -> ' in head: head,ret=head.rsplit(' -> ',1)
        # A block-opener colon may sit right before the return-type arrow
        # ("to name: -> type:") or at the end of the params ("to name with
        # x: -> type:"); it is never part of an identifier, so drop one.
        head=head.rstrip()
        if head.endswith(':'): head=head[:-1].rstrip()
        if ' with ' in head: name,ps=head.split(' with ',1); params=[p.strip() for p in split_top(ps) if p.strip()]
        else: name=head; params=[]
        body,j=child_block(lines,i+1,ind); return FunctionDef(line_no,name,params,ret,body),j
    if text.startswith('match '): return parse_match(lines,i,ind)
    return ExprStmt(line_no,parse_expr(text,line_no)),i+1

def _parse_record_key(s,line):
    s=s.strip()
    if (len(s)>=2 and s[0]==s[-1] and s[0] in "'\""):
        return s[1:-1]
    if __import__('re').match(r'^[A-Za-z_]\w*$',s): return s
    raise ParseError(f'bad record pattern key "{s}"', line, token=s)

def parse_pattern(s,line):
    s=s.strip()
    if s.startswith('[') and s.endswith(']'):
        inner=s[1:-1].strip(); items=[]
        if inner:
            for e in split_top(inner):
                e=e.strip()
                if not e: raise ParseError('empty pattern in list', line, token=s)
                if e.startswith('...'):
                    name=e[3:].strip()
                    if not __import__('re').match(r'^[A-Za-z_]\w*$',name):
                        raise ParseError(f'bad rest name "{name}"', line, token=name)
                    if any(isinstance(x,MatchRest) for x in items):
                        raise ParseError('only one "...rest" allowed in a list pattern', line, token=s)
                    items.append(MatchRest(line,name))
                else:
                    if items and isinstance(items[-1],MatchRest):
                        raise ParseError('"...rest" must be the last pattern in [...]', line, token=s)
                    items.append(parse_pattern(e,line))
        return MatchList(line,items)
    if s.startswith('{') and s.endswith('}'):
        inner=s[1:-1].strip(); fields=[]
        if inner:
            for e in split_top(inner):
                e=e.strip()
                ci=find_top_level(e,':')
                if ci==-1: raise ParseError(f'bad record pattern "{e}" -- use key: name', line, token=e)
                key=_parse_record_key(e[:ci],line)
                sub=e[ci+1:].strip()
                if not sub: raise ParseError(f'record pattern key "{key}" needs a pattern after ":"', line, token=e)
                fields.append((key,parse_pattern(sub,line)))
        return MatchRecord(line,fields)
    m=__import__('re').match(r'ok\s+([A-Za-z_]\w*)$',s)
    if m: return MatchOk(line,m.group(1))
    m=__import__('re').match(r'error\s+([A-Za-z_]\w*)$',s)
    if m: return MatchErr(line,m.group(1))
    e=parse_expr(s,line)
    if isinstance(e,LiteralExpr): return MatchLit(line,e.value)
    if isinstance(e,NameExpr): return MatchBind(line,e.name)
    raise ParseError(f'bad pattern "{s}"', line, token=s)

def _match_arm_start(lines,i,ind,line_no,kw):
    # Skip blank/comment lines; the first real line sets the arm indent.
    j=i+1
    while j<len(lines) and (not lines[j].strip() or lines[j].lstrip().startswith('#')): j+=1
    if j>=len(lines): raise ParseError('match needs at least one "when"', line_no, col=_kw_col(lines,i,ind,kw))
    arm_ind=indent_of(lines[j])
    if arm_ind<=ind: raise ParseError('match needs at least one "when"', line_no, col=_kw_col(lines,i,ind,kw))
    return j,arm_ind

def parse_match_arms(lines,j,arm_ind,ind):
    # Parse `when`/`otherwise` arms; arms sit at indent arm_ind, `ind` is the
    # enclosing statement's indent (for diagnostics). Returns (cases, otherwise, j).
    cases=[]; otherwise=None
    while j<len(lines):
        t=lines[j].strip(); ln=j+1
        if not t or t.startswith('#'): j+=1; continue
        aind=indent_of(lines[j])
        if aind<arm_ind: break
        if aind>arm_ind: raise ParseError('unexpected indentation in match', ln, col=aind+1)
        if t.startswith('when '):
            require_colon(lines[j],ln)
            head=t[5:-1].strip(); guard=None
            gi=find_top_level(head,' if ')
            if gi==-1:
                # `when [a] if:` -- `if` with no condition and no trailing space
                gt=find_top_level(head,' if')
                if gt!=-1 and gt+3==len(head):
                    raise ParseError('"if" in "when" needs a condition', ln, token='if')
            if gi!=-1:
                gpart=head[gi+4:].strip()
                if not gpart: raise ParseError('"if" in "when" needs a condition', ln, token='if')
                guard=parse_expr(gpart,ln); head=head[:gi].strip()
            pats=[parse_pattern(p.strip(),ln) for p in split_top(head) if p.strip()]
            if not pats: raise ParseError('when needs a pattern', ln, col=_kw_col(lines,j,ind,'when '))
            body,k=child_block(lines,j+1,aind); cases.append(MatchCase(ln,pats,guard,body)); j=k
        elif t=='otherwise:':
            body,k=child_block(lines,j+1,aind); otherwise=body; j=k; break
        else: raise ParseError('expected "when ..." or "otherwise:" in match', ln, col=aind+1)
    return cases,otherwise,j

def parse_match(lines,i,ind):
    line_no=i+1; text=lines[i].strip()
    require_colon(lines[i],line_no); expr=parse_expr(text[6:-1].strip(),line_no)
    j,arm_ind=_match_arm_start(lines,i,ind,line_no,'match ')
    cases,otherwise,j=parse_match_arms(lines,j,arm_ind,ind)
    if not cases and otherwise is None:
        raise ParseError('match needs at least one "when"', line_no, col=_kw_col(lines,i,ind,'match '))
    return MatchStmt(line_no,expr,cases,otherwise),j

def parse_match_value(lines,i,ind,value_str,line_no):
    # A match used as an expression: `set x to match EXPR:` / `give back match
    # EXPR:` / `say match EXPR:` -- value_str is the stripped `match EXPR:`.
    # The arms are indented under this statement like a match statement's.
    require_colon(lines[i],line_no); expr=parse_expr(value_str[6:-1].strip(),line_no)
    j,arm_ind=_match_arm_start(lines,i,ind,line_no,'match ')
    cases,otherwise,j=parse_match_arms(lines,j,arm_ind,ind)
    if not cases and otherwise is None:
        raise ParseError('match needs at least one "when"', line_no, col=_kw_col(lines,i,ind,'match '))
    return MatchExpr(line_no,expr,cases,otherwise),j

def parse_if(lines,i,ind):
    branches=[]; otherwise=None; j=i
    while j<len(lines):
        text=lines[j].strip(); ln=j+1
        if text.startswith('if '):
            require_colon(lines[j],ln); body,k=child_block(lines,j+1,ind); branches.append((parse_expr(text[3:-1].strip(),ln),body)); j=k
        elif text.startswith('otherwise if '):
            require_colon(lines[j],ln); body,k=child_block(lines,j+1,ind); branches.append((parse_expr(text[13:-1].strip(),ln),body)); j=k
        elif text=='otherwise:':
            body,k=child_block(lines,j+1,ind); otherwise=body; j=k; break
        else: break
    return IfStmt(i+1,branches,otherwise),j

# Pratt parser
PRE={'or':1,'and':2,'is':3,'is not':3,'is in':3,'is bigger than':3,'is smaller than':3,'is at least':3,'is at most':3,'==':3,'!=':3,'<':3,'>':3,'<=':3,'>=':3,'+':4,'-':4,'*':5,'/':5,'%':5,'**':6}

def parse_expr(s,line):
    try:
        toks=lex_expr(s,line)
    except LexError as e:
        # A bad character is a parse error, not a traceback.
        raise ParseError(str(e), line)
    p=ExprParser(toks,line); result=p.parse()
    if p.peek().kind!='EOF':
        # Previously any trailing tokens the Pratt parser didn't consume
        # (e.g. an unsupported operator) were silently dropped, so malformed
        # expressions ran with a truncated meaning instead of failing.
        tok=p.peek()
        hint=str(tok.value) if tok.value!='' else None
        raise ParseError(f'unexpected {tok.value!r} after expression', line, token=hint)
    return result

class ExprParser:
    def __init__(self,toks,line): self.t=toks; self.i=0; self.line=line; self.last=None; self.last_real=None
    def peek(self): return self.t[self.i]
    def take(self): x=self.t[self.i]; self.i+=1; self.last=x; self.last_real=x if x.kind!='EOF' else self.last_real; return x
    def word(self,w): return self.peek().kind=='WORD' and self.peek().value==w
    def parse(self,minp=0):
        tok=self.take()
        if tok.kind=='NUMBER' or tok.kind=='STRING': left=LiteralExpr(self.line,tok.value)
        elif tok.kind=='WORD':
            if tok.value=='yes': left=LiteralExpr(self.line,True)
            elif tok.value=='no': left=LiteralExpr(self.line,False)
            elif tok.value=='nothing': left=LiteralExpr(self.line,None)
            elif tok.value=='not': left=UnaryExpr(self.line,'not',self.parse(7))
            elif tok.value=='length':
                if self.peek().kind=='WORD' and self.peek().value=='of':
                    self.take(); collection=self.parse(7); left=CallExpr(self.line,NameExpr(self.line,'length'),[collection])
                else: left=NameExpr(self.line,tok.value)
            elif tok.value=='item':
                left=self.parse(7)
            elif tok.value in ('random',):
                a=self.parse(7)
                if self.word('to'): self.take()
                b=self.parse(7); left=CallExpr(self.line,NameExpr(self.line,'random_int'),[a,b])
            elif tok.value=='numbers':
                a=self.parse(7)
                if self.word('to'): self.take()
                b=self.parse(7); left=CallExpr(self.line,NameExpr(self.line,'niko_range'),[a,b])
            else: left=NameExpr(self.line,tok.value)
        elif tok.kind=='OP' and tok.value=='-': left=UnaryExpr(self.line,'-',self.parse(7))
        elif tok.kind=='OP' and tok.value=='(':
            left=self.parse();
            t=self.take()
            if t.value!=')': raise ParseError('expected )', self.line, token=str(t.value) if t.value!='' else '(')
        elif tok.kind=='OP' and tok.value=='[':
            items=[]
            if self.peek().value!=']':
                while True:
                    items.append(self.parse())
                    if self.peek().value!=',': break
                    self.take()
            self.take(); left=ListExpr(self.line,items)
        elif tok.kind=='OP' and tok.value=='{':
            items=[]
            if self.peek().value!='}':
                while True:
                    k=self.take(); key=k.value
                    t=self.take()
                    if t.value!=':': raise ParseError('expected : in record', self.line, token=str(t.value) if t.value!='' else '{')
                    items.append((key,self.parse()))
                    if self.peek().value!=',': break
                    self.take()
            self.take(); left=RecordExpr(self.line,items)
        else:
            if tok.kind=='EOF':
                # e.g. `say 1 +` -- point at the dangling operator if we can.
                hint=str(self.last_real.value) if self.last_real is not None else None
                raise ParseError('unexpected end of expression', self.line, token=hint)
            raise ParseError(f'unexpected {tok.value!r}', self.line, token=str(tok.value))
        while True:
            if self.peek().value=='(':
                self.take(); args=[]
                if self.peek().value!=')':
                    while True:
                        args.append(self.parse())
                        if self.peek().value!=',': break
                        self.take()
                self.take(); left=CallExpr(self.line,left,args); continue
            if self.peek().value=='[':
                self.take(); idx=self.parse(); self.take(); left=IndexExpr(self.line,left,idx); continue
            if self.peek().kind=='OP' and self.peek().value=='.':
                self.take(); name_tok=self.take()
                if name_tok.kind!='WORD': raise ParseError('expected a name after "."', self.line, token=str(name_tok.value) if name_tok.value!='' else '.')
                left=AttrExpr(self.line,left,name_tok.value); continue
            if self.peek().kind=='WORD' and self.peek().value=='of':
                # "N of LIST": `left` (already parsed) is the index, the
                # operand after "of" is the collection. item_of(i, x) expects
                # (index, collection) -- do not swap these two.
                self.take(); collection=self.parse(7); left=CallExpr(self.line,NameExpr(self.line,'item_of'),[left,collection]); continue
            if self.peek().kind=='WORD' and self.peek().value=='in':
                # handled as operator below
                pass
            start=self.i
            op=self._operator()
            if op is None or PRE.get(op,0)<minp:
                self.i=start
                break
            prec=PRE[op]; right=self.parse(prec+(0 if op=='**' else 1)); left=BinaryExpr(self.line,left,op,right)
        return left
    def _operator(self):
        t=self.peek()
        if t.kind=='OP' and t.value in PRE: self.take(); return t.value
        if t.kind=='WORD':
            candidates=['is not','is in','is bigger than','is smaller than','is at least','is at most','and','or','is']
            for c in candidates:
                parts=c.split(); ok=True
                for k,w in enumerate(parts):
                    q=self.t[self.i+k]
                    if q.kind!='WORD' or q.value!=w: ok=False; break
                if ok:
                    self.i+=len(parts); return c
        return None
