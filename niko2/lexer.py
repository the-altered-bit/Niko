from dataclasses import dataclass
import re

@dataclass
class Token:
    kind: str
    value: object
    line: int
    col: int

TOKEN_RE = re.compile(r'''(?P<WS>[ \t]+)|(?P<NUMBER>\d+(?:\.\d+)?)|(?P<STRING>"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')|(?P<OP>==|!=|<=|>=|\*\*|->|[+\-*/%<>=(),\[\]{}:.])|(?P<WORD>[A-Za-z_][A-Za-z0-9_]*)|(?P<COMMENT>\#.*)''')

class LexError(Exception): pass

def lex_expr(text, line, base_col=1):
    out=[]; pos=0
    while pos < len(text):
        m=TOKEN_RE.match(text,pos)
        if not m: raise LexError(f"unexpected character {text[pos]!r}")
        pos=m.end(); kind=m.lastgroup; val=m.group()
        if kind in ('WS','COMMENT'): continue
        col=base_col+m.start()
        if kind=='NUMBER': val=float(val) if '.' in val else int(val)
        elif kind=='STRING':
            import ast; val=ast.literal_eval(val); kind='STRING'
        elif kind=='WORD': kind='WORD'
        out.append(Token(kind,val,line,col))
    out.append(Token('EOF','',line,len(text)+base_col))
    return out
