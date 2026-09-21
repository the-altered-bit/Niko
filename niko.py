#!/usr/bin/env python3
"""
Niko - a programming language that reads like English.

Run a program:        python niko.py hello.niko
Try things live:      python niko.py            (opens the Niko REPL)
See the Python:       python niko.py hello.niko --show

Niko turns each line into Python, so anything Python can do, Niko can do
(bring in any Python library with:  use math).
"""
import builtins
import datetime
import json
import math
import re
import sys
import time
import traceback
import random as _random

STR = re.compile(r'("(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\')')


class NikoRuntimeError(Exception):
    """An error whose message is already written for humans."""


class NikoError(Exception):
    def __init__(self, line, msg):
        super().__init__(msg)
        self.line, self.msg = line, msg


# ---------------------------------------------------------------- helpers ---
def fmt(v, nested=False):
    if v is None:
        return 'nothing'
    if v is True:
        return 'yes'
    if v is False:
        return 'no'
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False) if nested else v
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(v)
    if isinstance(v, (list, tuple)):
        return '[' + ', '.join(fmt(x, True) for x in v) + ']'
    if isinstance(v, dict):
        return '{' + ', '.join(f'{k}: {fmt(x, True)}' for k, x in v.items()) + '}'
    return str(v)


def say(*args):
    print(' '.join(fmt(a) for a in args))


def ask(prompt=''):
    return input(fmt(prompt))


def ask_number(prompt=''):
    while True:
        t = input(fmt(prompt)).strip()
        try:
            return int(t)
        except ValueError:
            try:
                return float(t)
            except ValueError:
                print('Please type a number.')


def _range(a, b):
    return list(builtins.range(a, b + 1)) if a <= b else list(builtins.range(a, b - 1, -1))


def _round(x, d=0):
    f = 10 ** d
    r = math.floor(x * f + 0.5) / f
    return int(r) if d == 0 else r


def _number(x):
    try:
        return int(x)
    except (ValueError, TypeError):
        try:
            return float(x)
        except (ValueError, TypeError):
            raise NikoRuntimeError(f'I can\'t turn {fmt(x, True)} into a number.')


def _minmax(fn):
    return lambda *a: fn(a[0] if len(a) == 1 and isinstance(a[0], (list, tuple)) else a)


def _remove(lst, x):
    if x in lst:
        lst.remove(x)
    return lst


def _pos(l, n):
    if isinstance(n, float) and n.is_integer():
        n = int(n)
    if not isinstance(n, int) or not 1 <= n <= len(l):
        raise IndexError("That position isn't in the list (positions start at 1).")
    return n - 1


def _nth(l, n):
    return l[_pos(l, n)]


def _setnth(l, n, v):
    l[_pos(l, n)] = v


def _split(t, sep=' '):
    return list(str(t)) if sep == '' else str(t).split(sep)


def _average(l):
    if not l:
        raise NikoRuntimeError("I can't average an empty list.")
    return sum(l) / len(l)


def _pick(l):
    if not l:
        raise NikoRuntimeError("I can't pick from an empty list.")
    return _random.choice(l)


def _count_of(c, x):
    return str(c).count(x) if isinstance(c, str) else list(c).count(x)


def _read_file(name):
    try:
        with open(name, encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        raise NikoRuntimeError(f'I couldn\'t find the file "{name}".')


def _write_file(name, text):
    with open(name, 'w', encoding='utf-8') as f:
        f.write(fmt(text))


def _append_file(name, text):
    with open(name, 'a', encoding='utf-8') as f:
        f.write(fmt(text))


def _sleep(s):
    time.sleep(s)


# Names people may call. Used for "did you mean ...?" hints.
HELPER_NAMES = ['abs', 'append_file', 'average', 'ceil', 'count_of', 'ends_with', 'file_exists', 'floor', 'has',
                'join', 'keys', 'lower', 'max', 'min', 'now', 'number', 'pi', 'pick', 'random', 'read_file',
                'read_lines', 'remove', 'replace', 'reversed', 'round', 'say', 'ask', 'sleep', 'sorted', 'split',
                'sqrt', 'starts_with', 'sum', 'text', 'today', 'trim', 'unique', 'upper', 'write_file']

LIB = {
    'yes': True, 'no': False, 'nothing': None,
    'say': say, 'ask': ask, 'ask_number': ask_number,
    '__nth': _nth, '__setnth': _setnth,
    'range': _range, '__range': builtins.range,
    'random': lambda a, b: _random.randint(a, b),
    'has': lambda c, x: x in c,
    'keys': lambda o: list(o.keys()),
    'remove': _remove,
    'text': fmt, 'number': _number,
    'upper': lambda s: str(s).upper(), 'lower': lambda s: str(s).lower(),
    'trim': lambda s: str(s).strip(),
    'replace': lambda t, a, b: str(t).replace(a, b),
    'starts_with': lambda t, p: str(t).startswith(p),
    'ends_with': lambda t, p: str(t).endswith(p),
    'count_of': _count_of,
    'round': _round, 'floor': math.floor, 'ceil': math.ceil, 'sqrt': math.sqrt, 'abs': abs, 'pi': math.pi,
    'max': _minmax(max), 'min': _minmax(min), 'sum': sum, 'average': _average,
    'sorted': lambda l: sorted(l), 'reversed': lambda l: list(l)[::-1],
    'unique': lambda l: list(dict.fromkeys(l)), 'pick': _pick,
    'join': lambda l, sep=' ': sep.join(fmt(x) for x in l),
    'split': _split,
    'read_file': _read_file, 'write_file': _write_file, 'append_file': _append_file,
    'file_exists': lambda n: __import__('os').path.exists(n),
    'read_lines': lambda n: _read_file(n).splitlines(),
    'today': lambda: datetime.date.today().isoformat(),
    'now': lambda: datetime.datetime.now().strftime('%H:%M:%S'),
    'sleep': _sleep,
}


def _distance(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def suggest(name, names):
    best, best_d = None, 99
    for c in sorted(set(names) | set(HELPER_NAMES)):
        if c == name:
            continue
        d = _distance(name, c)
        if d < best_d:
            best, best_d = c, d
    return best if best is not None and best_d <= 2 and best_d < len(name) else None


# -------------------------------------------------------------- translator ---
def strip_comment(s):
    parts = STR.split(s)
    for i in range(0, len(parts), 2):
        k = parts[i].find('#')
        if k >= 0:
            parts[i] = parts[i][:k]
            return ''.join(parts[:i + 1]).rstrip()
    return s


def words(expr):
    """Turn Niko wording inside an expression into Python (text in quotes is left alone)."""
    strs = []

    def hide(m):
        strs.append(m.group(0))
        return f'__q{len(strs) - 1}__'

    p = STR.sub(hide, expr)
    p = re.sub(r'\b(\w+) is in (\w+(?:\([^()]*\)|\[[^\[\]]*\])?)', r'has(\2, \1)', p)
    p = re.sub(r'\bis not\b', '!=', p)
    p = re.sub(r'\bis bigger than\b', '>', p)
    p = re.sub(r'\bis smaller than\b', '<', p)
    p = re.sub(r'\bis at least\b', '>=', p)
    p = re.sub(r'\bis at most\b', '<=', p)
    p = re.sub(r'\bis\b', '==', p)
    p = re.sub(r'\blength of (\w+(?:\([^()]*\)|\[[^\[\]]*\])?)', r'len(\1)', p)
    p = re.sub(r'\bitem (\w+) of (\w+(?:\([^()]*\)|\[[^\[\]]*\])?)', r'__nth(\2, \1)', p)  # counting starts at 1
    p = re.sub(r'\bnumbers ([\w.]+) to ([\w.]+)', r'range(\1, \2)', p)
    p = re.sub(r'\brandom ([\w.]+) to ([\w.]+)', r'random(\1, \2)', p)
    return re.sub(r'__q(\d+)__', lambda m: strs[int(m.group(1))], p)


def translate(b, c):
    W = words

    def assign(t, expr, op='='):
        if c['in_fn'] and t in c['globals'] and t not in c['params']:
            return f'globals()["{t}"] {op} {expr}'
        return f'{t} {op} {expr}'

    m = re.fullmatch(r'set item (\w+) of (\w+) to (.+)', b)
    if m: return f'__setnth({m[2]}, {m[1]}, {W(m[3])})'
    m = re.fullmatch(r'set ([\w.\[\]"\']+) to (.+)', b)
    if m: return assign(m[1], W(m[2]))
    m = re.fullmatch(r'add (.+) to (\w+)', b)
    if m: return assign(m[2], W(m[1]), '+=')
    m = re.fullmatch(r'take (.+) from (\w+)', b)
    if m: return assign(m[2], W(m[1]), '-=')
    m = re.fullmatch(r'remove (.+) from (\w+)', b)
    if m: return f'remove({m[2]}, {W(m[1])})'
    m = re.fullmatch(r'put (.+) in (\w+)', b)
    if m: return f'{m[2]}.append({W(m[1])})'
    if b == 'say': return 'say()'
    m = re.fullmatch(r'say (.+)', b)
    if m: return f'say({W(m[1])})'
    m = re.fullmatch(r'ask number (.+) into (\w+)', b)
    if m: return assign(m[2], f'ask_number({W(m[1])})')
    m = re.fullmatch(r'ask (.+) into (\w+)', b)
    if m: return assign(m[2], f'ask({W(m[1])})')
    m = re.fullmatch(r'if (.+):', b)
    if m: return f'if {W(m[1])}:'
    m = re.fullmatch(r'otherwise if (.+):', b)
    if m: return f'elif {W(m[1])}:'
    if b == 'otherwise:': return 'else:'
    if b == 'repeat forever:': return 'while True:'
    m = re.fullmatch(r'repeat (.+) times:', b)
    if m: return f'for _ in __range({W(m[1])}):'
    m = re.fullmatch(r'for each (\w+) in (.+):', b)
    if m: return f'for {m[1]} in {W(m[2])}:'
    m = re.fullmatch(r'while (.+):', b)
    if m: return f'while {W(m[1])}:'
    m = re.fullmatch(r'to (\w+) with (.+):', b)
    if m: return f'def {m[1]}({m[2]}):'
    m = re.fullmatch(r'to (\w+):', b)
    if m: return f'def {m[1]}():'
    if b == 'give back': return 'return'
    m = re.fullmatch(r'give back (.+)', b)
    if m: return f'return {W(m[1])}'
    if b == 'stop': return 'break'
    if b == 'skip': return 'continue'
    m = re.fullmatch(r'use (\w+)', b)
    if m: return f'import {m[1]}'
    if re.match(r'(else|elif)\b', b):
        raise SyntaxError('Use "otherwise" (or "otherwise if") instead of "%s"' % re.split(r'\W', b)[0])
    if b.endswith(':'):
        raise SyntaxError("I don't understand this line")
    return W(b)


def transpile(src, globals_=None, names=None):
    """Turn Niko source into Python source. Keeps line numbers the same."""
    globals_ = set() if globals_ is None else globals_
    names = set() if names is None else names
    lines = src.replace('\r', '').split('\n')
    info, fn_stack = [], []
    for n, line in enumerate(lines, 1):
        indent = len(line) - len(line.lstrip())
        body = strip_comment(line.strip())
        l = {'n': n, 'raw': line, 'body': body, 'indent': indent, 'in_fn': False, 'params': []}
        if body:
            while fn_stack and indent <= fn_stack[-1][0]:
                fn_stack.pop()
            l['in_fn'] = bool(fn_stack)
            l['params'] = [p for _, ps in fn_stack for p in ps]
            m = re.fullmatch(r'to (\w+)(?: with (.+))?:', body)
            if m:
                params = [p.strip() for p in (m[2] or '').split(',') if p.strip()]
                names.add(m[1])
                names.update(params)
                fn_stack.append((indent, params))
            else:
                m = (re.fullmatch(r'set (\w+) to .+', body)
                     or re.fullmatch(r'ask (?:number )?.+ into (\w+)', body)
                     or re.fullmatch(r'for each (\w+) in .+:', body))
                if m:
                    names.add(m[1])
                    if not l['in_fn']:
                        globals_.add(m[1])
        info.append(l)
    out = []
    last_at = {}  # indent -> the last instruction seen at that indent
    for l in info:
        if not l['body']:
            out.append('')
            continue
        for k in [k for k in last_at if k > l['indent']]:
            del last_at[k]
        if l['body'].startswith('otherwise') and not re.match(r'(if |otherwise if )', last_at.get(l['indent'], '')):
            raise NikoError(l['n'], '"otherwise" needs an "if" right above it')
        last_at[l['indent']] = l['body']
        indent = l['raw'][:len(l['raw']) - len(l['raw'].lstrip())]
        try:
            code = translate(l['body'], {'in_fn': l['in_fn'], 'params': l['params'], 'globals': globals_})
        except SyntaxError as e:
            raise NikoError(l['n'], e.msg)
        out.append(indent + code)
    return '\n'.join(out)


# ------------------------------------------------------------------ runner ---
def friendly(e, names=()):
    msg = str(e)
    if isinstance(e, NikoRuntimeError):
        return msg
    m = re.match(r"name '(\w+)' is not defined", msg)
    if m:
        s = suggest(m[1], names)
        return f'I don\'t know what "{m[1]}" is. ' + (f'Did you mean "{s}"?' if s else 'Did you "set" it first?')
    if isinstance(e, UnboundLocalError) or 'referenced before assignment' in msg or 'where it is not associated' in msg:
        return 'You used a name before giving it a value. Use "set" first.'
    if isinstance(e, ZeroDivisionError):
        return "You can't divide by zero."
    if isinstance(e, IndexError):
        return "That position isn't in the list (positions start at 1)."
    if isinstance(e, KeyError):
        return f'There is no key {msg} in that record.'
    if isinstance(e, TypeError) and ('unsupported operand' in msg or 'can only concatenate' in msg):
        return 'You mixed text and numbers. Use text(...) to turn a number into text.'
    return f'{type(e).__name__} - {msg}'


def run(src, show=False):
    lines = src.split('\n')
    names = set()
    try:
        py = transpile(src, names=names)
    except NikoError as e:
        print(f"Oops - line {e.line}: {e.msg}  ->  {lines[e.line - 1].strip()}")
        return 1
    if show:
        print(py)
        return 0
    env = dict(LIB, __name__='__main__')
    try:
        code = compile(py, '<niko>', 'exec')
    except SyntaxError as e:
        n = e.lineno or 1
        print(f"Oops - line {n} doesn't make sense: {lines[n - 1].strip() if n <= len(lines) else ''}")
        return 1
    try:
        exec(code, env)
    except KeyboardInterrupt:
        print('\nStopped.')
    except BrokenPipeError:
        return 0
    except EOFError:
        print('\n(the program wanted more input)')
    except Exception as e:
        hits = [f.lineno for f in traceback.extract_tb(e.__traceback__) if f.filename == '<niko>']
        where = f' on line {hits[-1]}' if hits else ''
        print(f'Oops{where}: {friendly(e, names)}')
        return 1
    return 0


# -------------------------------------------------------------------- REPL ---
HELP = """Niko REPL - type a line and press Enter.
  say "hi"                  show something
  set x to 5                make a variable (it stays for the whole session)
  x * 2                     just type an expression to see its value
  if x is 5:                lines ending in ":" continue on the next line;
      say "five"            finish the block with an empty line
  exit                      leave (or press Ctrl+D)"""


def repl():
    try:
        import readline  # noqa: F401  (arrow-key history where available)
    except ImportError:
        pass
    print('Niko REPL. Type help for tips, exit to leave.')
    env, globals_, names = dict(LIB, __name__='__main__'), set(), set()
    while True:
        try:
            line = input('niko> ')
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            continue
        s = line.strip()
        if not s:
            continue
        if s in ('exit', 'quit'):
            return 0
        if s == 'help':
            print(HELP)
            continue
        chunk = [line]
        if s.endswith(':'):
            while True:
                try:
                    more = input('...   ')
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
                if not more.strip():
                    break
                chunk.append(more)
        src = '\n'.join(chunk)
        try:
            py = transpile(src, globals_, names)
        except NikoError as e:
            print(f'Oops: {e.msg}')
            continue
        try:
            if len(chunk) == 1:
                try:
                    code = compile(py, '<niko>', 'eval')
                except SyntaxError:
                    code = None
                if code is not None:
                    value = eval(code, env)
                    if value is not None:
                        print(fmt(value, True))
                    continue
            exec(compile(py, '<niko>', 'exec'), env)
        except SyntaxError as e:
            print("Oops: that line doesn't make sense to me.")
        except KeyboardInterrupt:
            print('\nStopped.')
        except EOFError:
            print('\n(no more input)')
        except Exception as e:
            print(f'Oops: {friendly(e, names)}')


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        sys.exit(repl())
    with open(args[0], encoding='utf-8') as f:
        sys.exit(run(f.read(), show='--show' in sys.argv))
