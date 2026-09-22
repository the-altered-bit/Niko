#!/usr/bin/env python3
"""Alpha 35: Niko 1 -> Niko 2 migration advisor.

``niko2 migrate <file.niko>`` analyzes a Niko 1 program and reports every
construct that Niko 2 rejects (``error``), that runs but behaves
differently (``warning``), or that deserves a remark (``note``). Each
finding cites a section of MIGRATION_GUIDE.md, which is the difference
catalog this tool is built from.

Two phases:

1. Heuristic scan -- line-based, using Niko 1's own lexical rules
   (string literals masked, ``#`` comments stripped). Covers the Niko 1
   idioms that never reach Niko 2's checker (they break parsing) and
   every warning-level difference the checker cannot see.
2. Checker phase -- the file is parsed and typechecked with Niko 2's own
   pipeline; the first diagnostic is mapped to a finding. Precise and
   heuristic-free. A generic checker diagnostic never clobbers a specific
   heuristic finding (which may carry a ``--fix`` rewrite).

``niko2 migrate --fix`` additionally applies the mechanical fixes (every
finding that carries one) and re-analyzes, so the fixed findings are
gone from the report.

This is an advisor, not a transpiler: ``--fix`` covers only rewrites
with identical semantics. Everything else is reported for a human to
decide. Niko 1 itself is never modified.
"""

import re

GUIDE = "MIGRATION_GUIDE.md"


class Finding:
    """One migration issue. ``fix`` is a function raw_line -> new_raw_line,
    or None when the finding needs a human. Fixes must recompute any
    positions from the line they are given (never reuse positions captured
    at detection time) and be idempotent."""

    __slots__ = ("line", "severity", "code", "message", "fix")

    def __init__(self, line, severity, code, message, fix=None):
        self.line = line
        self.severity = severity
        self.code = code
        self.message = message
        self.fix = fix

    def key(self):
        return (self.line, self.severity, self.code)

    def __repr__(self):
        return (f"Finding(line={self.line}, severity={self.severity!r}, "
                f"code={self.code!r})")


# ---------------------------------------------------------------------------
# Lexical helpers: Niko 1's own rules (strings masked, # comments stripped)
# ---------------------------------------------------------------------------

def _mask_line(line):
    """Return (masked, spans).

    ``masked`` has every string literal and ``#`` comment replaced by
    spaces, so columns stay stable and regexes never see inside strings.
    ``spans`` is a list of (start, end) for the string literals found
    (quotes included).
    """
    out = list(line)
    spans = []
    i, n = 0, len(line)
    while i < n:
        c = line[i]
        if c == "#":
            for j in range(i, n):
                out[j] = " "
            break
        if c in "\"'":
            j = i + 1
            while j < n:
                if line[j] == "\\":
                    j += 2
                    continue
                if line[j] == c:
                    break
                j += 1
            end = min(j + 1, n)
            spans.append((i, end))
            for j in range(i, end):
                out[j] = " "
            i = end
            continue
        i += 1
    return "".join(out), spans


def _indent_of(line):
    return len(line) - len(line.lstrip(" "))


def _split_top(s):
    """Split on top-level commas (strings/brackets aware)."""
    parts, depth, cur = [], 0, ""
    instr, q, i = False, "", 0
    while i < len(s):
        c = s[i]
        if instr:
            cur += c
            if c == "\\" and i + 1 < len(s):
                cur += s[i + 1]
                i += 2
                continue
            if c == q:
                instr = False
        elif c in "\"'":
            instr, q = True, c
            cur += c
        elif c in "([{":
            depth += 1
            cur += c
        elif c in ")]}":
            depth -= 1
            cur += c
        elif c == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += c
        i += 1
    parts.append(cur)
    return parts


def _match_paren(s, open_idx):
    """Index of the paren matching s[open_idx] == '(', or -1."""
    depth, instr, q, i = 0, False, "", open_idx
    while i < len(s):
        c = s[i]
        if instr:
            if c == "\\":
                i += 2
                continue
            if c == q:
                instr = False
        elif c in "\"'":
            instr, q = True, c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


# ---------------------------------------------------------------------------
# Per-line heuristic rules.
#
# Each rule is (code, severity, detect) where detect(
#   masked, raw, spans, line_no, ctx) returns (message, fix) or None.
# ---------------------------------------------------------------------------

_BOOL_HINT = re.compile(
    r"\bis\b|==|!=|<=|>=|<|>|\band\b|\bor\b|^\s*not\b"
    r"|\bhas\s*\(|\bis_ok\s*\(|\bis_error\s*\(|\bstarts_with\s*\(|\bends_with\s*\("
)

_N2_VALUE_BUILTINS = frozenset(
    # niko2.typecheck.BUILTIN_FUNCS: builtins Niko 2 refuses as values
    # ('pi' is exempt there too -- it is a plain number).
    "text number length item_of upper lower trim replace split join sorted "
    "reversed unique sum average max min abs ceil floor round sqrt random_int "
    "niko_range has today now sleep keys starts_with ends_with count_of pick "
    "write_file append_file read_file read_lines file_exists ok error is_ok "
    "is_error unwrap unwrap_or error_message try_read_file try_number".split()
)
_N1_ONLY_NAMES = frozenset(
    # Names a Niko 1 program may use as values that do not exist at all
    # in Niko 2 (statement-only or renamed).
    ["say", "ask", "ask_number", "remove", "range", "random"]
)

_USE_SUGGESTIONS = {
    "math": 'drop it and use the builtins (sqrt, pi, floor, ceil, abs, round) or import "stdlib/math.niko" as m for clamp/gcd/factorial and friends',
    "json": 'use import "stdlib/json.niko" as json (json.parse / json.stringify)',
    "random": 'use the random_int / "random A to B" builtin syntax',
    "time": 'use the sleep / today / now builtins',
    "os": 'no equivalent -- Niko 2 has no Python interop; file work uses read_file / write_file / append_file / file_exists',
    "sys": 'no equivalent -- Niko 2 has no Python interop',
    "re": 'no equivalent -- Niko 2 has no regular expressions; see the text stdlib module',
}

_GUIDE = "MIGRATION_GUIDE.md"


def _r_cond_boolean(masked, raw, spans, ln, ctx):
    m = re.match(r"\s*(if|while)\b(.*?):\s*$", masked)
    if not m:
        m = re.match(r"\s*otherwise\s+if\b(.*?):\s*$", masked)
        if not m:
            return None
    cond = m.group(2).strip() if m.lastindex == 2 else m.group(1).strip()
    if not cond:
        # A masked-out condition means a string literal: `if "x":`.
        raw_cond = raw[m.start(2) : m.end(2)].strip() if m.lastindex == 2 else raw[m.start(1) : m.end(1)].strip()
        if re.fullmatch(r'"[^"]*"|\'[^\']*\'', raw_cond):
            return (
                f"Niko 2 requires a boolean condition ({_GUIDE} §2.1): "
                f"`{raw_cond}` is text, not a boolean -- this is a "
                "type error. Compare it explicitly, e.g. "
                f'`if {raw_cond} is not "":`.',
                None,
            )
        return None
    if _BOOL_HINT.search(cond):
        return None
    if re.fullmatch(r"(yes|no)", cond):
        return None
    # `in` / `not in` are owned by the in-operator rule.
    if re.search(r"(?<![A-Za-z_])in(?![A-Za-z_])", cond):
        return None
    return (
        f"Niko 2 requires a boolean condition ({_GUIDE} §2.1): "
        f"`{cond}` fails typecheck unless it holds a boolean. Use an "
        'explicit comparison, e.g. `if name is not "":`.',
        None,
    )


def _r_not_boolean(masked, raw, spans, ln, ctx):
    m = re.search(r"\bnot\s+(.+)$", masked)
    if not m:
        return None
    operand = m.group(1).strip()
    if operand.endswith(":"):
        operand = operand[:-1].strip()
    if not operand:
        return None
    if re.match(r"in\b", operand):
        return None  # `not in` -- owned by the in-operator rule
    # `not ( ... )` and `not <known-boolean-shape>` are fine.
    if operand.startswith("(") or _BOOL_HINT.search(operand):
        return None
    if re.fullmatch(r"(yes|no)", operand):
        return None
    return (
        f"Niko 2's `not` needs a boolean operand ({_GUIDE} §2.2): "
        f"`not {operand}` fails typecheck. Parenthesize a comparison, "
        'e.g. `not (name is "")`.',
        None,
    )


def _r_repeat_forever(masked, raw, spans, ln, ctx):
    if re.match(r"\s*repeat\s+forever\s*:", masked):
        return (
            f"Niko 2 has no `repeat forever:` ({_GUIDE} §3.1).",
            lambda r: " " * _indent_of(r) + "while yes:",
        )
    return None


def _r_use_python(masked, raw, spans, ln, ctx):
    m = re.match(r"\s*use\s+([A-Za-z_]\w*)\s*$", masked)
    if not m:
        return None
    lib = m.group(1)
    hint = _USE_SUGGESTIONS.get(
        lib,
        "no Python interop in Niko 2 -- rewrite using builtins or the "
        f"stdlib (see {_GUIDE} §7)",
    )
    return (
        f"Niko 1's `use {lib}` imports the Python library `{lib}`; Niko 2's "
        "`use` only includes Niko files, so this statement is silently "
        f"ignored ({_GUIDE} §3.2). {hint}.",
        None,
    )


def _r_ask_call(masked, raw, spans, ln, ctx):
    if not re.search(r"\bask(_number)?\s*\(", masked):
        return None

    def fix(r):
        m = re.match(
            r"(\s*)set\s+([A-Za-z_]\w*)\s+to\s+ask(_number)?\s*\((.*)\)\s*$", r
        )
        if not m:
            return r
        ind, name, num, prompt = (
            m.group(1),
            m.group(2),
            m.group(3) or "",
            m.group(4),
        )
        return f"{ind}ask{num} {prompt} into {name}"

    simple = (
        re.match(r"\s*set\s+[A-Za-z_]\w*\s+to\s+ask(_number)?\s*\(.*\)\s*$", raw)
        is not None
    )
    return (
        f"Niko 2's `ask` is a statement, not a call ({_GUIDE} §3.3): "
        '`ask("...")` fails to parse. Write `ask "..." into name`.',
        fix if simple else None,
    )


def _r_remove_call(masked, raw, spans, ln, ctx):
    if not re.search(r"\bremove\s*\(", masked):
        return None

    def fix(r):
        mm, _ = _mask_line(r)
        m = re.match(r"\s*remove\s*\((.*)\)\s*$", mm)
        if not m:
            return r
        args = _split_top(mm[m.start(1) : m.end(1)])
        if len(args) != 2:
            return r
        # Re-slice the raw line using the masked arg spans (columns align).
        a0_raw = r[m.start(1) : m.start(1) + len(args[0])].strip()
        a1_raw = r[m.start(1) + len(args[0]) + 1 : m.end(1)].strip()
        return f"{' ' * _indent_of(r)}remove {a1_raw} from {a0_raw}"

    stmt_form = re.match(r"\s*remove\s*\(.*\)\s*$", raw) is not None
    return (
        f"Niko 2's `remove` is a statement, not a call ({_GUIDE} §3.4): "
        "`remove(list, x)` fails to parse. Write `remove x from list`.",
        fix if stmt_form else None,
    )


def _r_random_call(masked, raw, spans, ln, ctx):
    if not re.search(r"\brandom\s*\(", masked):
        return None

    def fix(r):
        mm, _ = _mask_line(r)
        m = re.search(r"\brandom\s*\(", mm)
        if not m:
            return r
        open_idx = mm.index("(", m.start())
        close_idx = _match_paren(mm, open_idx)
        if close_idx == -1:
            return r
        args = _split_top(mm[open_idx + 1 : close_idx])
        if len(args) != 2:
            return r
        head = r[: m.start()].rstrip()
        a0 = r[open_idx + 1 : open_idx + 1 + len(args[0])].strip()
        a1 = r[open_idx + 1 + len(args[0]) + 1 : close_idx].strip()
        sep = " " if head else ""
        return f"{head}{sep}random {a0} to {a1}"

    return (
        f"Niko 2 has no `random(a, b)` call form ({_GUIDE} §3.5): "
        "write `random a to b`.",
        fix,
    )


def _r_range_call(masked, raw, spans, ln, ctx):
    if not re.search(r"\brange\s*\(", masked):
        return None

    def fix(r):
        mm, _ = _mask_line(r)
        m = re.search(r"\brange\s*\(", mm)
        if not m:
            return r
        return r[: m.start()] + "niko_range(" + r[m.end() :]

    return (
        f"Niko 2 renamed `range(a, b)` to `niko_range(a, b)` ({_GUIDE} "
        "§3.6); semantics are unchanged (inclusive, auto-descending).",
        fix,
    )


def _r_in_operator(masked, raw, spans, ln, ctx):
    if re.match(r"\s*(for\b|put\b)", masked):
        return None  # `for` has its own rule; `put X in L` is fine in Niko 2
    hits_notin = False
    found = False
    for m in re.finditer(r"(?<![A-Za-z_])in(?![A-Za-z_])", masked):
        before = masked[: m.start()]
        if re.search(r"\bis\s*$", before):
            continue
        found = True
        if re.search(r"\bnot\s*$", before):
            hits_notin = True
    if not found:
        return None
    if hits_notin:
        return (
            f"Niko 2 has no `not in` ({_GUIDE} §3.7): rewrite `X not in Y` "
            "as `not (X is in Y)`.",
            None,
        )

    def fix(r):
        mm, _ = _mask_line(r)
        if re.match(r"\s*(for\b|put\b)", mm):
            return r
        pieces, last = [], 0
        for m in re.finditer(r"(?<![A-Za-z_])in(?![A-Za-z_])", mm):
            before = mm[: m.start()]
            if re.search(r"\bis\s*$", before) or re.search(
                r"\bnot\s*$", before
            ):
                continue
            pieces.append(r[last : m.start()])
            pieces.append("is in")
            last = m.end()
        pieces.append(r[last:])
        return "".join(pieces)

    return (
        f"Niko 2 has no bare `in` operator ({_GUIDE} §3.7): "
        "write `x is in y`.",
        fix,
    )


def _r_py_for(masked, raw, spans, ln, ctx):
    if not re.match(r"\s*for\b(?!\s+each\b)", masked):
        return None

    def fix(r):
        return re.sub(r"^(\s*)for\b(?!\s+each\b)", r"\1for each", r, count=1)

    return (
        f"Niko 2 has no bare `for` ({_GUIDE} §3.20): Niko 1 fell through "
        "to Python here. Write `for each x in y:`.",
        fix,
    )


def _r_py_assign(masked, raw, spans, ln, ctx):
    m = re.match(r"(\s*)([A-Za-z_]\w*)\s*=(?![=])\s*(.+?)\s*$", masked)
    if not m:
        return None

    def fix(r):
        mm, _ = _mask_line(r)
        m2 = re.match(r"(\s*)([A-Za-z_]\w*)\s*=(?![=])\s*(.+?)\s*$", mm)
        if not m2:
            return r
        if "=" in m2.group(3):  # chained `x = y = 5` -- not mechanical
            return r
        indent = r[: m2.start(2)]
        name = r[m2.start(2) : m2.end(2)]
        value = r[m2.start(3) :]
        return f"{indent}set {name} to {value}"

    name = m.group(2)
    chained = "=" in m.group(3)
    return (
        f"Niko 2 has no `=` assignment statement ({_GUIDE} §3.8): "
        f"write `set {name} to ...`.",
        None if chained else fix,
    )


def _r_py_augassign(masked, raw, spans, ln, ctx):
    m = re.match(r"\s*([A-Za-z_]\w*)\s*(\+=|-=|\*=|/=)\s*(.+?)\s*$", masked)
    if not m:
        return None
    op = m.group(2)

    def fix(r):
        mm, _ = _mask_line(r)
        m2 = re.match(r"\s*([A-Za-z_]\w*)\s*(\+=|-=|\*=|/=)\s*(.+?)\s*$", mm)
        if not m2:
            return r
        nm = r[m2.start(1) : m2.end(1)]
        op2 = m2.group(2)
        val = r[m2.start(3) :]
        ind = r[: m2.start(1)]
        if op2 == "+=":
            return f"{ind}add {val} to {nm}"
        if op2 == "-=":
            return f"{ind}take {val} from {nm}"
        sym = "*" if op2 == "*=" else "/"
        return f"{ind}set {nm} to {nm} {sym} {val}"

    equiv = {
        "+=": "`add X to name`",
        "-=": "`take X from name`",
        "*=": "`set name to name * X`",
        "/=": "`set name to name / X`",
    }[op]
    return (
        f"Niko 2 has no `{op}` ({_GUIDE} §3.21): Niko 1 fell through to "
        f"Python here. Write {equiv}.",
        fix,
    )


def _r_py_import(masked, raw, spans, ln, ctx):
    if re.match(r"\s*import\s*\"", masked):
        return None  # Niko 2 module import -- fine
    if re.match(r"\s*(import|from)\b", masked):
        return (
            f"Niko 2 has no Python `import` ({_GUIDE} §3.9): Niko 1 fell "
            "through to Python here, but Niko 2 has no Python interop. "
            'Use `import "file.niko" as m`, `use "file.niko"`, or a `pkg:` '
            "package instead.",
            None,
        )
    return None


def _r_fstring(masked, raw, spans, ln, ctx):
    if re.search(r"\bf(['\"])", raw):
        return (
            f"Niko 2 has no f-strings ({_GUIDE} §3.10): build the text "
            'with `+` and `text()`, e.g. `say "n is " + text(n)`.',
            None,
        )
    return None


def _r_hex_literal(masked, raw, spans, ln, ctx):
    if not re.search(r"\b0[xX]([0-9a-fA-F]+)\b", masked):
        return None
    return (
        f"Niko 2 has no hex literals ({_GUIDE} §3.11).",
        lambda r: re.sub(
            r"\b0[xX]([0-9a-fA-F]+)\b",
            lambda h: str(int(h.group(1), 16)),
            r,
        ),
    )


_FLOORDIV_OP = re.compile(
    r"([A-Za-z_]\w*|\d+(?:\.\d+)?|(?<![A-Za-z_0-9\)])\([^()]*\))"
    r"\s*//\s*"
    r"([A-Za-z_]\w*|\d+(?:\.\d+)?|(?<![A-Za-z_0-9\)])\([^()]*\))"
)


def _r_floordiv(masked, raw, spans, ln, ctx):
    if "//" not in masked:
        return None

    def fix(r):
        mm, _ = _mask_line(r)
        m = _FLOORDIV_OP.search(mm)
        if not m:
            return r
        return (
            r[: m.start()]
            + f"floor({r[m.start(1):m.end(1)]} / {r[m.start(2):m.end(2)]})"
            + r[m.end() :]
        )

    return (
        f"Niko 2 has no `//` operator ({_GUIDE} §3.12).",
        fix,
    )


def _r_slice_syntax(masked, raw, spans, ln, ctx):
    if re.search(r"\[[^\[\]]*:[^\[\]]*\]", masked):
        return (
            f"Niko 2 has no slice syntax ({_GUIDE} §3.13): `s[1:3]` fails "
            "to parse. Loop with `item N of` / `length of` instead.",
            None,
        )
    return None


def _r_say_adjacent(masked, raw, spans, ln, ctx):
    if not re.match(r"\s*say\b", masked):
        return None
    if not any(
        raw[e1:s2].strip() == ""
        for (_, e1), (s2, _) in zip(spans, spans[1:])
    ):
        return None

    def fix(r):
        _, sps = _mask_line(r)
        for (_, e1), (s2, _) in zip(sps, sps[1:]):
            if r[e1:s2].strip() == "":
                return r[:e1] + " + " + r[s2:]
        return r

    return (
        'Adjacent strings after `say` implicitly concatenate in Niko 1 '
        f"({_GUIDE} §3.14); Niko 2 rejects them.",
        fix,
    )


def _r_to_default_arg(masked, raw, spans, ln, ctx):
    m = re.match(r"\s*to\b.*\bwith\b(.*)", masked)
    if m and "=" in m.group(1):
        return (
            f"Niko 2 function headers take no default arguments ({_GUIDE} "
            "§3.16): `to f with a=1:` does not bind `a`. Give the default "
            "inside the body instead.",
            None,
        )
    return None


def _r_set_dotted(masked, raw, spans, ln, ctx):
    if not re.search(
        r"\bset\s+[A-Za-z_]\w*\.[A-Za-z_]\w*\s+to\b", masked
    ):
        return None

    def fix(r):
        mm, _ = _mask_line(r)
        m = re.search(
            r"\bset\s+([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s+to\b", mm
        )
        if not m:
            return r
        rec = r[m.start(1) : m.end(1)]
        key = r[m.start(2) : m.end(2)]
        return r[: m.start()] + f'set {rec}["{key}"] to' + r[m.end() :]

    return (
        f"Niko 2 cannot assign through `.` ({_GUIDE} §3.17): "
        'write `set d["k"] to ...`.',
        fix,
    )


def _r_join_arity(masked, raw, spans, ln, ctx):
    m = re.search(r"\bjoin\s*\(", masked)
    if not m:
        return None
    open_idx = masked.index("(", m.start())
    close_idx = _match_paren(masked, open_idx)
    if close_idx == -1:
        return None
    if len(_split_top(masked[open_idx + 1 : close_idx])) != 1:
        return None

    def fix(r):
        mm, _ = _mask_line(r)
        m2 = re.search(r"\bjoin\s*\(", mm)
        if not m2:
            return r
        oi = mm.index("(", m2.start())
        ci = _match_paren(mm, oi)
        if ci == -1 or len(_split_top(mm[oi + 1 : ci])) != 1:
            return r
        return r[:ci] + ', " "' + r[ci:]

    return (
        f"Niko 2's `join` needs a separator argument ({_GUIDE} §3.18): "
        '`join(list)` is an arity error; Niko 1 defaulted to `" "`.',
        fix,
    )


def _r_add_list_extend(masked, raw, spans, ln, ctx):
    if re.match(r"\s*add\s+\[", masked):
        return (
            f"Niko 1's `add [..] to list` extends the list, but Niko 2's "
            f"`add` is arithmetic-only ({_GUIDE} §3.15). Extend it with a "
            "`for each` loop and `put ... in` instead.",
            None,
        )
    return None


def _r_bool_arith(masked, raw, spans, ln, ctx):
    if re.search(
        r"\b(yes|no)\b\s*(\*\*|[-+*/%])|(\*\*|[-+*/%])\s*\b(yes|no)\b", masked
    ):
        return (
            f"Niko 2 arithmetic needs numbers ({_GUIDE} §2.4): "
            "`yes`/`no` in `+ - * / %` is a type error (Niko 1 computed "
            "with 1/0). Use 1/0 explicitly.",
            None,
        )
    return None


def _r_str_mul(masked, raw, spans, ln, ctx):
    for s, e in spans:
        before = raw[:s].rstrip()
        after = raw[e:].lstrip()
        if (before.endswith("*") and not before.endswith("**")) or (
            after.startswith("*") and not after.startswith("**")
        ):
            return (
                f"Niko 2 has no string repetition ({_GUIDE} §2.4): "
                '`"ab" * 3` is a type error. Repeat in a loop instead.',
                None,
            )
    return None


def _r_builtin_as_value(masked, raw, spans, ln, ctx):
    m = re.match(r"\s*set\s+[A-Za-z_]\w*\s+to\s+([A-Za-z_]\w*)\s*$", masked)
    if not m:
        return None
    name = m.group(1)
    if name in _N2_VALUE_BUILTINS:
        return (
            f"Niko 2 builtins are not values ({_GUIDE} §2.3): "
            f"`set x to {name}` is a type error. Call it, or wrap it in a "
            "`to` function.",
            None,
        )
    if name in _N1_ONLY_NAMES:
        return (
            f"`{name}` is not a Niko 2 builtin at all ({_GUIDE} §3.19): "
            "Niko 1 let you alias it as a value.",
            None,
        )
    return None


# ----- warnings ------------------------------------------------------------


def _r_text_render(masked, raw, spans, ln, ctx):
    if re.search(r"\btext\s*\(", masked):
        return (
            f"Warning: `text()` renders differently on Niko 2's VM/WASM "
            f"({_GUIDE} §4.1): `text(yes)` is `\"True\"` (Niko 1: `\"yes\"`), "
            '`text(1.0)` is `"1.0"` (Niko 1: `"1"`), `text(nothing)` is '
            '`"None"`.',
            None,
        )
    return None


def _r_join_render(masked, raw, spans, ln, ctx):
    if re.search(r"\bjoin\s*\(", masked):
        return (
            f"Warning: Niko 2's `join` renders elements with `str()` "
            f"({_GUIDE} §4.2): `join([yes], \"-\")` is `\"True\"` (Niko 1: "
            '`"yes"`), `join([2.0], \"-\")` is `"2.0"` (Niko 1: `"2"`).',
            None,
        )
    return None


def _r_now_format(masked, raw, spans, ln, ctx):
    if re.search(r"\bnow\s*\(\s*\)", masked):
        return (
            f"Warning: Niko 2's `now()` returns a full ISO datetime "
            f"({_GUIDE} §4.3); Niko 1 returned `HH:MM:SS`.",
            None,
        )
    return None


def _r_round_half(masked, raw, spans, ln, ctx):
    if re.search(r"\bround\s*\(", masked):
        return (
            f"Warning: Niko 2's `round()` is banker's rounding ({_GUIDE} "
            "§4.4): `round(2.5)` is `2` (Niko 1: `3`).",
            None,
        )
    return None


def _r_split_empty(masked, raw, spans, ln, ctx):
    m = re.search(r"\bsplit\s*\(", masked)
    if not m:
        return None
    oi = masked.index("(", m.start())
    ci = _match_paren(masked, oi)
    if ci == -1:
        return None
    # An empty string literal (""/'') inside the parens: the spans give
    # (start, end) with quotes included, so length 2 means empty.
    for s, e in spans:
        if oi < s and e <= ci and e - s == 2:
            return (
                f"Warning: `split(t, \"\")` raises on Niko 2 ({_GUIDE} "
                "§4.5); Niko 1 split it into characters.",
                None,
            )
    return None


def _r_item_zero(masked, raw, spans, ln, ctx):
    if re.search(r"\bitem\s+0\s+of\b", masked):
        return (
            f"Warning: `item 0 of x` raises on Niko 1 but wraps to the "
            f"last element on Niko 2 ({_GUIDE} §4.6).",
            None,
        )
    return None


def _r_list_index(masked, raw, spans, ln, ctx):
    for m in re.finditer(r"[A-Za-z_0-9\])\]]\[([^\[\]]*)\]", masked):
        # d["key"] is a map-key access -- identical in both. A `:` means a
        # slice, which the slice-syntax rule owns.
        if raw[m.start() + 2 :].lstrip().startswith(('"', "'")):
            continue
        if ":" in m.group(1):
            continue
        return (
            f"Warning: `x[i]` indexing differs ({_GUIDE} §4.7): Niko 1 is "
            "0-based; Niko 2 lists are 1-based and wrap (strings stay "
            "0-based). If this indexes a list, it reads a different "
            "element.",
            None,
        )
    return None


_CMP = re.compile(
    r"is\s+not\s+in|is\s+bigger\s+than|is\s+smaller\s+than|is\s+at\s+least|"
    r"is\s+at\s+most|is\s+in|is\s+not|(?<![A-Za-z_])is(?![A-Za-z_])|"
    r"==|!=|<=|>=|<|>"
)


def _r_chain_compare(masked, raw, spans, ln, ctx):
    # Niko 1 has no generics; on Niko 2 code, `list<number>` would fake two
    # comparisons via `<`/`>`. Strip generic shapes (iteratively, for
    # nesting like `list<list<number>>`).
    nomask = masked
    while True:
        new = re.sub(r"[A-Za-z_]\w*<[^<>]*>", "", nomask)
        if new == nomask:
            break
        nomask = new
    for clause in re.split(r"\band\b|\bor\b", nomask):
        clause = re.sub(r"^\s*not\b", "", clause)
        if len(_CMP.findall(clause)) >= 2:
            return (
                f"Warning: Niko 2 does not chain comparisons ({_GUIDE} "
                "§4.8): `a is b is c` parses as `(a is b) is c`. Niko 1 "
                "chained like Python.",
                None,
            )
    return None


def _not_in_already_paren(mm, m, m2):
    """True when the line is already `not (... is in ...)` -- i.e. the `(`
    after `not` opens a group that spans the `is in` and closes at the end
    of the line (before an optional `:`)."""
    between = mm[m.end() : m2.start()]
    if not between.strip().startswith("("):
        return False
    moi = mm.index("(", m.end())
    ci = _match_paren(mm, moi)
    return ci != -1 and mm[ci + 1 :].strip() in ("", ":")


def _r_not_is_in(masked, raw, spans, ln, ctx):
    m = re.search(r"\bnot\b", masked)
    m2 = re.search(r"\bis\s+in\b", masked)
    if not m or not m2 or m2.start() < m.end():
        return None
    if _not_in_already_paren(masked, m, m2):
        return None

    def fix(r):
        mm, _ = _mask_line(r)
        m = re.search(r"\bnot\b", mm)
        m2 = re.search(r"\bis\s+in\b", mm)
        if not m or not m2 or m2.start() < m.end():
            return r
        if _not_in_already_paren(mm, m, m2):
            return r
        head = r[: m.start()]
        x = r[m.end() : m2.start()].strip()
        tail_m = mm[m2.end() :]
        cm = re.search(r":\s*$", tail_m)
        if cm:
            y = r[m2.end() : m2.end() + cm.start()].strip()
            after = r[m2.end() + cm.start() :]
        else:
            y = r[m2.end() :].strip()
            after = ""
        if not x or not y:
            return r
        return f"{head}not ({x} is in {y}){after}"

    return (
        f"Warning: `not` binds tighter than `is in` on Niko 2 ({_GUIDE} "
        "§4.9): `not x is in y` parses as `(not x) is in y`, but Niko 1 "
        "read it as `not (x is in y)`.",
        fix,
    )


_RULES = [
    # (code, severity, detect)
    ("cond-boolean", "error", _r_cond_boolean),
    ("not-boolean", "error", _r_not_boolean),
    ("repeat-forever", "error", _r_repeat_forever),
    ("use-python", "error", _r_use_python),
    ("ask-call", "error", _r_ask_call),
    ("remove-call", "error", _r_remove_call),
    ("random-call", "error", _r_random_call),
    ("range-call", "error", _r_range_call),
    ("in-operator", "error", _r_in_operator),
    ("py-for", "error", _r_py_for),
    ("py-assign", "error", _r_py_assign),
    ("py-augassign", "error", _r_py_augassign),
    ("py-import", "error", _r_py_import),
    ("fstring", "error", _r_fstring),
    ("hex-literal", "error", _r_hex_literal),
    ("floordiv", "error", _r_floordiv),
    ("slice-syntax", "error", _r_slice_syntax),
    ("say-adjacent", "error", _r_say_adjacent),
    ("to-default-arg", "error", _r_to_default_arg),
    ("set-dotted", "error", _r_set_dotted),
    ("join-arity", "error", _r_join_arity),
    ("add-list-extend", "error", _r_add_list_extend),
    ("bool-arith", "error", _r_bool_arith),
    ("str-mul", "error", _r_str_mul),
    ("builtin-as-value", "error", _r_builtin_as_value),
    ("text-render", "warning", _r_text_render),
    ("join-render", "warning", _r_join_render),
    ("now-format", "warning", _r_now_format),
    ("round-half", "warning", _r_round_half),
    ("split-empty", "warning", _r_split_empty),
    ("item-zero", "warning", _r_item_zero),
    ("list-index", "warning", _r_list_index),
    ("chain-compare", "warning", _r_chain_compare),
    ("not-is-in", "warning", _r_not_is_in),
]

_GUIDE_SECTIONS = {
    "cond-boolean": "2.1",
    "not-boolean": "2.2",
    "builtin-as-value": "2.3",
    "bool-arith": "2.4",
    "str-mul": "2.4",
    "n2-diagnostic": "2.5",
    "repeat-forever": "3.1",
    "use-python": "3.2",
    "use-in-function": "3.2",
    "ask-call": "3.3",
    "remove-call": "3.4",
    "random-call": "3.5",
    "range-call": "3.6",
    "in-operator": "3.7",
    "py-assign": "3.8",
    "py-import": "3.9",
    "fstring": "3.10",
    "hex-literal": "3.11",
    "floordiv": "3.12",
    "slice-syntax": "3.13",
    "say-adjacent": "3.14",
    "add-list-extend": "3.15",
    "to-default-arg": "3.16",
    "set-dotted": "3.17",
    "join-arity": "3.18",
    "unknown-name": "3.19",
    "py-for": "3.20",
    "py-augassign": "3.21",
    "text-render": "4.1",
    "join-render": "4.2",
    "now-format": "4.3",
    "round-half": "4.4",
    "split-empty": "4.5",
    "item-zero": "4.6",
    "list-index": "4.7",
    "chain-compare": "4.8",
    "not-is-in": "4.9",
    "global-write": "4.10",
    "logic-note": "5.1",
}


class _Ctx:
    """Cross-line state for the heuristic scan."""

    def __init__(self, lines):
        self.lines = lines
        self.top_names = set()
        self._collect_top_names()

    def _collect_top_names(self):
        for line in self.lines:
            masked, _ = _mask_line(line)
            if _indent_of(line) != 0:
                continue
            m = re.match(r"\s*set\s+([A-Za-z_]\w*)\b", masked)
            if m:
                self.top_names.add(m.group(1))
                continue
            m = re.match(r"\s*ask\b.*\binto\s+([A-Za-z_]\w*)\s*$", masked)
            if m:
                self.top_names.add(m.group(1))
                continue
            m = re.match(r"\s*for\s+each\s+([A-Za-z_]\w*)\s+in\b", masked)
            if m:
                self.top_names.add(m.group(1))


def _heuristic_findings(lines):
    ctx = _Ctx(lines)
    findings = []
    # Function stack for the global-write / use-in-function rules:
    # (indent, params, locals).
    func_stack = []
    saw_logic = False
    logic_line = 0

    for ln, raw in enumerate(lines, 1):
        masked, spans = _mask_line(raw)
        if not masked.strip():
            continue
        ind = _indent_of(raw)
        while func_stack and ind <= func_stack[-1][0]:
            func_stack.pop()
        m = re.match(r"\s*to\s+([A-Za-z_]\w*)(?:\s+with\s+(.*?))?\s*:\s*$", masked)
        if m:
            params = set()
            if m.group(2):
                for p in m.group(2).split(","):
                    p = p.strip().split(":")[0].strip()
                    if re.fullmatch(r"[A-Za-z_]\w*", p):
                        params.add(p)
            func_stack.append((ind, params, set()))

        for code, severity, detect in _RULES:
            try:
                hit = detect(masked, raw, spans, ln, ctx)
            except Exception:
                hit = None
            if hit is None:
                continue
            message, fix = hit
            findings.append(Finding(ln, severity, code, message, fix))

        if func_stack:
            if re.match(r"\s*use\b", masked):
                findings.append(
                    Finding(
                        ln,
                        "error",
                        "use-in-function",
                        f"`use` inside a function is rejected by Niko 2 "
                        f"({_GUIDE} §3.2): move it to the top of the file.",
                    )
                )
            # global-write rule: rebinding a top-level name inside a function
            # writes a local on Niko 2, the global on Niko 1. (Mutating
            # forms -- `set item`, `put`, `remove` -- touch the same object
            # in both and are fine.)
            sm = re.match(r"\s*set\s+([A-Za-z_]\w*)\b", masked)
            am = re.match(r"\s*ask\b.*\binto\s+([A-Za-z_]\w*)\s*$", masked)
            fm = re.match(r"\s*for\s+each\s+([A-Za-z_]\w*)\s+in\b", masked)
            dm = None
            for cand in (
                re.match(r"\s*add\b\s*(.*?)\bto\s+([A-Za-z_]\w*)\s*$", masked),
                re.match(
                    r"\s*take\b\s*(.*?)\bfrom\s+([A-Za-z_]\w*)\s*$", masked
                ),
            ):
                # `add [..] to l` is the extend idiom -- the add-list-extend
                # error owns it.
                if cand and not cand.group(1).lstrip().startswith("["):
                    dm = cand
                    break
            _, params, locals_ = func_stack[-1]
            for mm, verb, gi in (
                (sm, "set", 1),
                (am, "set", 1),
                (fm, "set", 1),
                (dm, "update", 2),
            ):
                if mm:
                    nm = mm.group(gi)
                    if verb == "update":  # add/take: name the real statement
                        kw = "add" if "add" in mm.re.pattern else "take"
                        tail = "to" if kw == "add" else "from"
                        what = f"`{kw} ... {tail} {nm}`"
                    else:
                        what = f"`{verb} {nm}`"
                    if (
                        nm in ctx.top_names
                        and nm not in params
                        and nm not in locals_
                    ):
                        findings.append(
                            Finding(
                                ln,
                                "warning",
                                "global-write",
                                f"Warning: {what} here writes a "
                                "function-local on Niko 2, but Niko 1 wrote "
                                "the top-level variable "
                                f"({_GUIDE} §4.10). Pass it as a parameter "
                                "or restructure.",
                            )
                        )
                    else:
                        locals_.add(nm)

        if not saw_logic and re.search(r"\b(and|or)\b", masked):
            saw_logic = True
            logic_line = ln

    if saw_logic:
        findings.append(
            Finding(
                logic_line,
                "note",
                "logic-note",
                "Note: `and`/`or` match Niko 1 exactly on Niko 2 "
                "(short-circuit, returning the deciding operand's value) "
                f"since Alpha 30 -- no action needed ({_GUIDE} §5.1).",
            )
        )
    findings.sort(key=lambda f: (f.line, f.severity, f.code))
    return findings


# ---------------------------------------------------------------------------
# Checker phase: Niko 2's own parse + typecheck, first diagnostic mapped
# ---------------------------------------------------------------------------

_CHECKER_CODE_HINTS = (
    ("condition must be boolean", "cond-boolean"),
    ("not requires a boolean", "not-boolean"),
    ("can't use the builtin", "builtin-as-value"),
    ("'use' is only allowed at the top of a file", "use-in-function"),
    ("import must be at the top of the file", "py-import"),
)


def _checker_finding(lines, name):
    """Run Niko 2's own pipeline; map the first diagnostic to a Finding."""
    try:
        from .cli import compile_source
    except Exception:
        return None
    src = "\n".join(lines)
    try:
        compile_source(src, name)
        return None
    except Exception as e:  # ParseError / Diagnostic
        msg = getattr(e, "message", None) or str(e)
        line = getattr(e, "line", None) or 1
        line = max(1, min(line, len(lines))) if lines else 1
        text = lines[line - 1] if lines else ""
        masked, _ = _mask_line(text)
        for hint, code in _CHECKER_CODE_HINTS:
            if hint in msg:
                return Finding(
                    line,
                    "error",
                    code,
                    f"Niko 2 reports: {msg} "
                    f"({_GUIDE} §{_GUIDE_SECTIONS[code]}).",
                )
        if '"add ... to" needs numbers' in msg and re.match(
            r"\s*add\s+\[", masked
        ):
            return Finding(
                line,
                "error",
                "add-list-extend",
                f"Niko 2 reports: {msg} "
                f"({_GUIDE} §{_GUIDE_SECTIONS['add-list-extend']}).",
            )
        m = re.search(r'unknown name "(\w+)"', msg)
        if m:
            nm = m.group(1)
            if nm == "range" and re.search(r"\brange\s*\(", masked):
                code = "range-call"
            elif nm in ("ask", "ask_number") and re.search(
                r"\bask(_number)?\s*\(", masked
            ):
                code = "ask-call"
            elif nm == "remove" and re.search(r"\bremove\s*\(", masked):
                code = "remove-call"
            elif nm == "forever" and re.match(r"\s*repeat\s+forever", masked):
                code = "repeat-forever"
            elif nm in ("say", "ask", "ask_number", "remove", "range", "random"):
                code = "builtin-as-value"
            else:
                code = "unknown-name"
            if code == "unknown-name":
                message = (
                    f'Niko 2 reports: {msg} ({_GUIDE} §3.19). The name may '
                    "be a Niko 1-only builtin or a Python fallthrough."
                )
            else:
                message = (
                    f"Niko 2 reports: {msg} "
                    f"({_GUIDE} §{_GUIDE_SECTIONS[code]})."
                )
            return Finding(line, "error", code, message)
        return Finding(
            line,
            "error",
            "n2-diagnostic",
            f"Niko 2 reports: {msg} ({_GUIDE} §2.5).",
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Error codes the checker cannot see: Niko 2 accepts the file but the
# program is still broken (`use <python-lib>` is silently ignored;
# `join`'s arity is enforced at run time, not check time).
# These survive the clean-check validation in analyze_source.
_SILENT_ERRORS = frozenset(["use-python", "join-arity"])


def analyze_source(src, name="<memory>"):
    """Return a sorted list of Findings for the Niko 1 program ``src``."""
    lines = src.split("\n")
    findings = _heuristic_findings(lines)
    chk = _checker_finding(lines, name)
    if chk is None:
        # Niko 2's own parse+check accepts the file, so every heuristic
        # *error* is a false positive -- except the ones the checker cannot
        # see. Warnings/notes describe behavior, not rejection, so they
        # stand regardless.
        findings = [
            f
            for f in findings
            if f.severity != "error" or f.code in _SILENT_ERRORS
        ]
    else:
        heur_here = [f for f in findings if f.line == chk.line]
        if any(f.code == chk.code for f in heur_here):
            pass  # heuristic already said it (and may carry a fix)
        elif chk.code in ("n2-diagnostic", "unknown-name"):
            # A generic checker diagnostic adds nothing when the heuristic
            # already names the problem precisely.
            if not any(f.severity == "error" for f in heur_here):
                findings.append(chk)
        else:
            # A specific checker diagnosis the heuristic missed.
            findings.append(chk)
    findings.sort(key=lambda f: (f.line, f.severity, f.code))
    by_key = {}
    for f in findings:
        k = (f.line, f.code)
        if k not in by_key or (by_key[k].fix is None and f.fix is not None):
            by_key[k] = f
    return sorted(by_key.values(), key=lambda f: (f.line, f.severity, f.code))


def analyze_file(path):
    with open(path, encoding="utf8") as fh:
        return analyze_source(fh.read(), path)


def apply_fixes(src, findings):
    """Apply every fix carried by ``findings``. Returns (new_src, [codes]).

    Fixes are applied line by line, in rule order, to a fixpoint (bounded),
    so stacked findings on one line (e.g. ``x = random(1, 5)``) compose.
    Lines whose fix leaves the text unchanged are left alone.
    """
    lines = src.split("\n")
    by_line = {}
    for f in findings:
        if f.fix is not None:
            by_line.setdefault(f.line, []).append(f)
    applied = []
    for ln in sorted(by_line):
        idx = ln - 1
        if not (0 <= idx < len(lines)):
            continue
        cur = lines[idx]
        for _ in range(4):  # bounded fixpoint
            nxt = cur
            for f in by_line[ln]:
                try:
                    cand = f.fix(nxt)
                except Exception:
                    cand = nxt
                if cand != nxt:
                    nxt = cand
                    if f.code not in applied:
                        applied.append(f.code)
            if nxt == cur:
                break
            cur = nxt
        lines[idx] = cur
    return "\n".join(lines), applied


def format_finding(path, f):
    return f"{path}:{f.line}: {f.severity} [{f.code}] {f.message}"


def summarize(findings):
    n = {"error": 0, "warning": 0, "note": 0}
    for f in findings:
        n[f.severity] = n.get(f.severity, 0) + 1
    return n
