#!/usr/bin/env python3
"""Alpha 36: differential fuzzer for Niko 2's three backends (VM/WASM/native).

Generates random, deterministic Niko programs from a seeded grammar-aware
generator, runs them on every backend as a subprocess, and compares stdout
bytes exactly. Any disagreement is a failure: the program is shrunk
(greedy line deletion) and saved to ``fuzz_failures/`` with a report.

Usage:
    niko2 fuzz [--seed N] [--cases N] [--backend vm,wasm,native]
               [--timeout SEC] [--native-sample N] [--keep-passing]
               [--no-minimize] [--if-bias]
    niko2 fuzz --corpus DIR        # re-run saved *.niko cases (regression set)

Exit codes: 0 = every case agreed on all backends, 1 = divergences found,
2 = usage error.

Design notes (see ALPHA36_DESIGN.md for the full story):

* Generated programs use only stable, documented, deterministic language
  features: no wall-clock, no randomness, no file I/O, no pkg:/sleep.
  ``ask`` gets canned stdin (recorded next to saved failures).
* Every candidate is validated in-process (parse + typecheck) before it
  runs; invalid candidates are discarded and counted, never executed.
* Integers stay small (|v| < 2**53 by construction) and float output is
  never printed directly, so the two *documented* rendering divergences
  (f64 precision, integral-float ``text()``) cannot fire by construction.
  ``try_number`` is only generated with valid numeric inputs: its
  text-failure error message diverges across backends (VM builds the
  sentence, WASM/native emit the bare input) and the message stays
  observable through string ops on the bound error, so no output
  normalization could allowlist every derived form. The runner still
  carries a tiny expected-divergence allowlist (see
  ``EXPECTED_DIVERGENCES``) for anything that slips through.
* A backend that hangs past ``--timeout`` is a FAILURE (never silent).
* Termination by construction: while-loops use a dedicated counter that
  is never reassigned inside the body (a protected set blocks `set`,
  `add` and `take` on it); `for each`/`repeat` iterate over bounded
  literals, and `put`/`remove` never target a list an enclosing
  `for each` is iterating (growing it mid-loop never terminates);
  `stop`/`skip` are emitted freely inside loop bodies -- the Alpha 37
  compiler fix emits ITER_POP for `stop` out of a `repeat`/`for each`,
  so no nesting shape can leak a stale iterator (see `_gen_loop_exit`);
  `ask` never appears inside a loop or function body so the
  runtime ask count never exceeds the canned stdin lines; calls to
  exponential-time (fib) or huge-value (fact) functions always pass
  small literals (0..12); expression-level calls take terminal-only
  arguments so calls cannot nest inside expressions. A hang that
  survives all that is a real backend bug, not a generator artifact.
* The shrinker (``minimize``) is greedy line deletion: it keeps deleting
  single lines while the program still parses/checks AND still fails with
  the identical backend signature. Limits: line-granular (it cannot shrink
  inside a line), greedy (local minima possible), and it assumes the
  divergence is deterministic.
"""

import argparse
import difflib
import os
import random
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# in-process validation: parse + typecheck, no subprocess
# ---------------------------------------------------------------------------

def valid_program(src):
    """True iff src parses and typechecks (what `niko2 run` requires).

    Mirrors niko2.cli.compile_source: every top-level statement with a
    `name` attribute (set/ask/for/function/...) is pre-registered as ANY,
    so `for each` loop vars leak at module level exactly the way the real
    backends accept them.
    """
    try:
        from niko2.parser import parse as _parse
        from niko2 import typecheck as _tc
        tree = _parse(src)
        imported = [n.name for n in tree.body if hasattr(n, "name")]
        _tc.check(tree, imported)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# generator
# ---------------------------------------------------------------------------

INT_LITS = list(range(-999, 1000))
FLOAT_LITS = [0.5, 1.5, 2.25, -0.75, 3.125, -2.5, 10.5, 100.25, 0.125]
ASCII_WORDS = ["alpha", "beta", "gamma", "hello", "world", "niko", "fuzz",
               "quick brown fox", "x", "", "abc123", "a b c", "Niko-2"]
UNI_WORDS = ["h\u00e9llo", "w\u00f6rld", "\u65e5\u672c\u8a9e",
             "\U0001f389 party", "na\u00efve caf\u00e9", "\u03a9\u2248\u00e7\u221a",
             "\u4e2d\u6587\u6d4b\u8bd5"]
ESCAPED_TEXTS = ['"line1\\nline2"', '"tab\\there"', '"quote\\"q\\""',
                 '"back\\\\slash"', '"a\\nb\\nc"']
PROMPTS = ["name? ", "age? ", "city? ", "favorite color? ", "n? "]
CMP_OPS = ["is", "is not", "is smaller than", "is bigger than",
           "is at least", "is at most", "==", "!=", "<", ">", "<=", ">="]


class Gen:
    """Seeded grammar-aware Niko program generator.

    Tracks a tiny static type environment so the programs it emits pass the
    typechecker: every variable/function has a known type, indices stay in
    range, loops are bounded, and runtime errors are avoided by
    construction (no division by zero, no unwrap of errors, no OOB
    indexing).
    """

    def __init__(self, seed, if_bias=0):
        self.rng = random.Random(seed)
        self.seed = seed
        self.if_bias = if_bias  # >0: gen_if emits more `otherwise if`
        # branches (focused campaigns, e.g. Alpha 38's native otherwise-if
        # fix verification). 0 = default distribution.
        self.reset()

    def reset(self):
        self.vc = 0
        self.fc = 0
        self.env = {}      # name -> type tuple
        self.funcs = {}    # name -> (param_types, ret_type)
        self.expensive = set()  # funcs with exponential-time bodies (fib):
                                # call sites must use small literal args
        self.asks = []     # ['text'|'number', ...] in order
        self.ask_budget = 3
        self.protected = set()  # while-loop counters: never reassigned
        self.iterated = {}    # list var -> number of enclosing `for each`
                                # loops iterating it: `put`/`remove` must not
                                # target a var with a live count (growing
                                # the list mid-iteration never terminates on
                                # any backend). A count, not a set: nested
                                # loops can iterate the SAME variable, and a
                                # plain discard would wrongly clear the
                                # outer loop's claim (Alpha 37 fix).
        self.func_ret = None    # return type of the function whose body is
                                # being generated (None at top level): an
                                # early `give back` must produce this type,
                                # or the generator's return-type model
                                # (self.funcs) drifts from the checker's
                                # inference and ill-typed call sites slip
                                # through (e.g. min(int, <bool>), which the
                                # backends disagree on)

    # -- names ----------------------------------------------------------
    def new_var(self, prefix="v"):
        self.vc += 1
        return f"{prefix}{self.vc}"

    def new_func(self, prefix="f"):
        self.fc += 1
        return f"{prefix}{self.fc}"

    # -- types ----------------------------------------------------------
    # types are tuples: ('int',) ('float',) ('text', ascii_bool) ('bool',)
    # ('nothing',) ('list', elem) ('rec', {k: t}) ('result',) ('func',)
    # ('any',)
    def vars_of(self, t):
        return [n for n, t2 in self.env.items() if t2 == t]

    def any_var(self):
        return self.rng.choice(list(self.env)) if self.env else None

    # -- entry point ----------------------------------------------------
    def gen_program(self):
        self.reset()
        lines = []
        for _ in range(self.rng.randint(0, 2)):
            lines += self.gen_func_def(top=True)
        lines += self.gen_block(self.rng.randint(5, 12), 0,
                                top=True, in_loop=False, in_func=False)
        return "\n".join(lines) + "\n", self.stdin_text()

    def stdin_text(self):
        parts = []
        for kind in self.asks:
            if kind == "number":
                parts.append(str(self.rng.randint(-999, 9999)))
            else:
                parts.append(self.rng.choice(
                    ASCII_WORDS + UNI_WORDS + ["", "hello world", "42"]))
        return "\n".join(parts) + "\n" if parts else ""

    # -- blocks ---------------------------------------------------------
    def _gen_loop_exit(self):
        # Alpha 37: the VM iterator-leak bug is fixed -- the compiler now
        # emits ITER_POP for `stop` out of a `repeat`/`for each`
        # (compiler.py), so `stop` pops the innermost loop's iterator
        # before jumping to the loop end and no nesting shape can leave
        # a stale iterator behind. `skip` jumps to the live loop head and
        # was always safe. Both are emitted freely wherever a loop
        # encloses the statement (this is only called with in_loop=True).
        return self.rng.choice(["stop", "skip"])

    def gen_block(self, n, indent, top, in_loop, in_func, depth=3):
        # Block scoping (matches the checker): `set` inside an if/while/
        # for-body/repeat/match-arm block creates a binding that is NOT
        # visible after the block; reassignments of outer names stay.
        # `for each` loop vars belong to the enclosing block (they are
        # added by gen_for before the body block runs).
        saved = dict(self.env)
        lines = []
        pad = "    " * indent
        nest_ok = depth > 0
        # Alpha 37 bias: heavier coverage of the fixed stop/iterator
        # space. Inside a loop, stop/skip exits go 0.06 -> 0.10 per
        # statement and each loop-statement branch widens (while/for
        # 0.06 -> 0.08, repeat 0.04 -> 0.08), so loop-in-loop shapes are
        # generated ~1.5x as often; the difference is funded by slightly
        # less gen_set / match / match-expr inside loops. Outside a loop
        # every cutoff is exactly Alpha 36's.
        if in_loop:
            cuts = (0.10, 0.14, 0.32, 0.42, 0.52, 0.60, 0.68, 0.76,
                    0.80, 0.84, 0.88, 0.92, 0.94)
        else:
            cuts = (0.06, 0.10, 0.32, 0.42, 0.52, 0.58, 0.64, 0.68,
                    0.74, 0.78, 0.82, 0.86, 0.90)
        (t_exit, t_give, t_set, t_say, t_if, t_while, t_for, t_repeat,
         t_match, t_call, t_ask, t_mut, t_mexpr) = cuts
        for _ in range(n):
            choice = self.rng.random()
            if in_loop and choice < t_exit:
                lines.append(pad + self._gen_loop_exit())
            elif in_func and choice < t_give:
                # early give back (function still ends with one; harmless
                # because it produces the function's own return type)
                lines.append(pad + f"give back {self.gen_expr(self.func_ret, 2)}")
            elif choice < t_set or not nest_ok:
                lines += self.gen_set(indent)
            elif choice < t_say:
                lines.append(pad + self.gen_say())
            elif choice < t_if:
                lines += self.gen_if(indent, in_loop, in_func, depth - 1)
            elif choice < t_while:
                lines += self.gen_while(indent, in_func, depth - 1)
            elif choice < t_for:
                lines += self.gen_for(indent, in_func, depth - 1, top)
            elif choice < t_repeat:
                lines += self.gen_repeat(indent, in_func, depth - 1)
            elif choice < t_match:
                lines += self.gen_match_stmt(indent, in_loop, in_func,
                                             depth - 1)
            elif choice < t_call and self.funcs:
                lines.append(pad + self.gen_call_stmt())
            elif choice < t_ask and not in_func and not in_loop \
                    and len(self.asks) < 3:
                # ask only outside loops/functions: every ask statement then
                # executes at most once, so the runtime ask count never
                # exceeds the canned stdin lines (stdin exhaustion diverges
                # across backends: VM errors, wasm text-ask yields "",
                # wasm number-ask reprompts forever).
                lines.append(pad + self.gen_ask())
            elif choice < t_mut:
                lines += self.gen_mutate(indent)
            elif choice < t_mexpr:
                lines += self.gen_match_expr_stmt(indent)
            else:
                lines += self.gen_set(indent)
        for name in list(self.env.keys()):
            if name not in saved:
                del self.env[name]
        return lines

    # -- statements -----------------------------------------------------
    def list_vars(self, elem=None):
        out = []
        for n, t in self.env.items():
            if t[0] == "list" and (elem is None or t[1] == elem):
                out.append(n)
        return out

    def gen_set(self, indent):
        pad = "    " * indent
        cands = [n for n in self.env if n not in self.protected]
        if cands and self.rng.random() < 0.35:
            name = self.rng.choice(cands)
            t = self.env[name]
            if t[0] == "list" and len(t) > 2:
                t = ("list", t[1])  # length unknown after mutation; regen
        else:
            name = self.new_var()
            t = self.rng.choice([("int",), ("int",), ("text", True),
                                 ("text", False), ("bool",),
                                 ("list", ("int",)), ("list", ("text", True)),
                                 ("rec", None)])
            if t[0] == "rec":
                t = ("rec", self.gen_rec_schema())
            elif t[0] == "list" and self.rng.random() < 0.3:
                t = ("list", self.rng.choice(
                    [("int",), ("text", True), ("bool",), ("any",)]))
        annot = ""
        if t[0] == "list":
            # build the literal here so the length is known (setitem safety)
            n = self.rng.randint(1, 4)
            elems = [self.gen_expr(t[1], 1) for _ in range(n)]
            expr = "[" + ", ".join(elems) + "]"
            self.env[name] = ("list", t[1], n)
        elif t[0] in ("int", "text", "bool") and self.rng.random() < 0.15:
            # Annotate only plain literals: the checker infers those
            # exactly. Compound expressions can involve ANY-typed builtin
            # calls (every builtin checks as ANY), which would break the
            # annotation (e.g. any+any infers as number, not text).
            if t[0] == "int":
                expr, annot = str(self.rng.randint(-999, 999)), ": number"
            elif t[0] == "bool":
                expr, annot = self.rng.choice(["yes", "no"]), ": boolean"
            else:
                expr, annot = _niko_str(self.rng.choice(ASCII_WORDS)), \
                    ": text"
            self.env[name] = t
        else:
            expr = self.gen_expr(t, 3)
            self.env[name] = t
        return [pad + f"set {name}{annot} to {expr}"]

    def gen_rec_schema(self):
        schema = {}
        for i in range(self.rng.randint(1, 3)):
            t = self.rng.choice([("int",), ("text", True), ("bool",)])
            schema[f"k{i}"] = t
        return schema

    def gen_say(self):
        n = self.rng.randint(1, 3)
        parts = []
        for _ in range(n):
            t = self.rng.choice([("int",), ("int",), ("text", True),
                                 ("text", False), ("bool",), ("nothing",),
                                 ("any",)])
            parts.append(self.gen_expr(t, 3))
        return "say " + ", ".join(parts)

    def gen_if(self, indent, in_loop, in_func, depth=3):
        pad = "    " * indent
        lines = [pad + f"if {self.gen_expr(('bool',), 3)}:"]
        lines += self.gen_block(self.rng.randint(1, 3), indent + 1,
                                top=False, in_loop=in_loop, in_func=in_func,
                                depth=depth)
        for _ in range(self.rng.randint(1, 4) if self.if_bias
                       else self.rng.randint(0, 2)):
            if self.rng.random() < (0.95 if self.if_bias else 0.7):
                lines.append(pad + f"otherwise if {self.gen_expr(('bool',), 3)}:")
                lines += self.gen_block(self.rng.randint(1, 3), indent + 1,
                                        top=False, in_loop=in_loop,
                                        in_func=in_func, depth=depth)
        if self.rng.random() < 0.6:
            lines.append(pad + "otherwise:")
            lines += self.gen_block(self.rng.randint(1, 3), indent + 1,
                                    top=False, in_loop=in_loop,
                                    in_func=in_func, depth=depth)
        return lines

    def gen_while(self, indent, in_func, depth=3):
        pad = "    " * indent
        c = self.new_var("c")
        k = self.rng.randint(1, 4)
        self.env[c] = ("int",)
        # Protect the counter while the body generates: a `set c to ...`
        # inside the body would reset it and hang the loop forever.
        self.protected.add(c)
        lines = [pad + f"set {c} to 0",
                 pad + f"while {c} is smaller than {k}:"]
        body = [pad + "    " + f"set {c} to {c} + 1"]
        body += self.gen_block(self.rng.randint(1, 3), indent + 1,
                               top=False, in_loop=True, in_func=in_func,
                               depth=depth)
        if self.rng.random() < 0.3:
            body.append(pad + "    " + f"say {c}")
        self.protected.discard(c)
        return lines + body

    def gen_for(self, indent, in_func, depth=3, top=False):
        pad = "    " * indent
        it = self.new_var("it")
        kind = self.rng.random()
        srcvar = None
        if kind < 0.4:
            a = self.rng.randint(-3, 6)
            b = self.rng.randint(-3, 6)
            src, et = f"numbers {a} to {b}", ("int",)
        elif kind < 0.7 and self.list_vars(("int",)):
            srcvar = self.rng.choice(self.list_vars(("int",)))
            src, et = srcvar, ("int",)
        elif kind < 0.85 and self.list_vars(("text", True)):
            srcvar = self.rng.choice(self.list_vars(("text", True)))
            src, et = srcvar, ("text", True)
        else:
            n = self.rng.randint(1, 4)
            elems = [self.gen_expr(("int",), 1) for _ in range(n)]
            src, et = "[" + ", ".join(elems) + "]", ("int",)
        self.env[it] = et
        if srcvar is not None:
            self.iterated[srcvar] = self.iterated.get(srcvar, 0) + 1
        lines = [pad + f"for each {it} in {src}:"]
        lines += self.gen_block(self.rng.randint(1, 3), indent + 1,
                                top=False, in_loop=True, in_func=in_func,
                                depth=depth)
        if srcvar is not None:
            left = self.iterated[srcvar] - 1
            if left:
                self.iterated[srcvar] = left
            else:
                del self.iterated[srcvar]
        # Loop var scope (matches the checker): visible after the loop only
        # when the `for` sits directly at module top level; everywhere else
        # it is scoped to the loop statement itself.
        if not (top and indent == 0):
            self.env.pop(it, None)
        return lines

    def gen_repeat(self, indent, in_func, depth=3):
        pad = "    " * indent
        lines = [pad + f"repeat {self.rng.randint(0, 5)} times:"]
        lines += self.gen_block(self.rng.randint(1, 3), indent + 1,
                                top=False, in_loop=True, in_func=in_func,
                                depth=depth)
        return lines

    def gen_match_stmt(self, indent, in_loop, in_func, depth=3):
        pad = "    " * indent
        subj_t = self._pick_match_subj()
        subj = self.gen_expr(subj_t, 2)
        lines = [pad + f"match {subj}:"]
        for _ in range(self.rng.randint(1, 3)):
            pat = self.gen_pattern(subj_t)
            saved = dict(self.env)
            self.env.update(self._pattern_bindings(pat, subj_t))
            guard = ""
            if self.rng.random() < 0.3:
                guard = f" if {self.gen_expr(('bool',), 2)}"
            lines.append(pad + f"    when {pat}{guard}:")
            lines += self.gen_block(self.rng.randint(1, 2), indent + 2,
                                    top=False, in_loop=in_loop,
                                    in_func=in_func, depth=depth)
            self.env = saved
        if self.rng.random() < 0.6:
            lines.append(pad + "    otherwise:")
            lines += self.gen_block(self.rng.randint(1, 2), indent + 2,
                                    top=False, in_loop=in_loop,
                                    in_func=in_func, depth=depth)
        return lines

    def gen_match_expr_stmt(self, indent):
        # `set v to match subj:` — the expression form (needs otherwise)
        pad = "    " * indent
        ipad = "    " * (indent + 1)
        apad = "    " * (indent + 2)
        subj_t = self.rng.choice([("int",), ("text", True), ("bool",)])
        subj = self.gen_expr(subj_t, 2)
        arm_t = self.rng.choice([("int",), ("text", True)])
        v = self.new_var()
        lines = [pad + f"set {v} to match {subj}:"]
        for _ in range(self.rng.randint(1, 2)):
            lines.append(ipad + f"when {self.gen_pattern(subj_t)}:")
            lines.append(apad + self.gen_expr(arm_t, 2))
        lines.append(ipad + "otherwise:")
        lines.append(apad + self.gen_expr(arm_t, 2))
        # define AFTER the arms: they must not reference v itself
        self.env[v] = arm_t
        return lines

    def gen_pattern(self, subj_t):
        k = subj_t[0]
        r = self.rng.random()
        if k == "int":
            if r < 0.4:
                return str(self.rng.randint(0, 9))
            if r < 0.6:
                a, b = self.rng.randint(0, 9), self.rng.randint(0, 9)
                return f"{a}, {b}"
            return self.new_var("m")
        if k == "text":
            if r < 0.5:
                return '"' + self.rng.choice(ASCII_WORDS).replace('"', "'") + '"'
            if r < 0.65:
                a = self.rng.choice(ASCII_WORDS)
                b = self.rng.choice(ASCII_WORDS)
                return f'"{a}", "{b}"'
            return self.new_var("m")
        if k == "bool":
            return self.rng.choice(["yes", "no", "yes, no"])
        if k == "list":
            return self.rng.choice(["[]", "[a]", "[a, b]", "[h, ...t]"])
        if k == "result":
            return self.rng.choice(["ok v", "error m"])
        if k == "rec":
            keys = list(subj_t[1]) if len(subj_t) > 1 and subj_t[1] else ["k0"]
            pick = keys[: self.rng.randint(1, len(keys))]
            return "{" + ", ".join(f"{k}: m_{k}" for k in pick) + "}"
        return self.new_var("m")

    def _bump_len(self, n, delta):
        t = self.env[n]
        if len(t) > 2 and t[2] is not None and delta is not None:
            self.env[n] = ("list", t[1], t[2] + delta)
        elif len(t) > 2:
            self.env[n] = ("list", t[1], None)

    def _pick_match_subj(self):
        cands = [("int",), ("text", True), ("bool",),
                 ("list", ("int",)), ("result",)]
        recs = [n for n, t in self.env.items() if t[0] == "rec"]
        if recs and self.rng.random() < 0.2:
            n = self.rng.choice(recs)
            return ("rec", self.env[n][1], n)
        return self.rng.choice(cands)

    def _pattern_bindings(self, pat, subj_t):
        """names bound by a match pattern -> their static types"""
        out = {}
        k = subj_t[0]
        if pat.startswith("{"):
            schema = subj_t[1] if len(subj_t) > 1 else {}
            for key in schema:
                if f"{key}:" in pat:
                    out[f"m_{key}"] = schema[key]
            return out
        if pat in ("ok v",):
            out["v"] = ("any",)
        elif pat in ("error m",):
            out["m"] = ("text",)
        elif pat in ("[a]", "[a, b]"):
            for nm in ("a", "b"):
                if nm in pat:
                    et = subj_t[1] if len(subj_t) > 1 else ("any",)
                    out[nm] = et
        elif pat == "[h, ...t]":
            et = subj_t[1] if len(subj_t) > 1 else ("any",)
            out["h"] = et
            out["t"] = ("list", et)
        elif pat == "[]":
            pass
        elif ", " in pat or pat in ("yes", "no"):
            pass  # literals / alternatives bind nothing
        elif pat and (pat[0].isalpha() or pat[0] == "_"):
            # bare-name binding (also "yes, no" handled above)
            if k == "int":
                out[pat] = ("int",)
            elif k == "text":
                out[pat] = ("text",)
            elif k == "bool":
                out[pat] = ("bool",)
            else:
                out[pat] = ("any",)
        return out

    def gen_ask(self):
        kind = self.rng.choice(["text", "number"])
        self.asks.append(kind)
        v = self.new_var()
        self.env[v] = ("int",) if kind == "number" else ("text", True)
        prompt = self.rng.choice(PROMPTS)
        if kind == "number":
            return f'ask number "{prompt}" into {v}'
        return f'ask "{prompt}" into {v}'

    def gen_mutate(self, indent):
        pad = "    " * indent
        cands = []
        if [n for n, t in self.env.items() if t[0] == "list"]:
            cands += ["put", "remove", "setitem"]
        if [n for n, t in self.env.items()
                if t == ("int",) and n not in self.protected]:
            cands += ["add", "take"]
        if [n for n, t in self.env.items() if t[0] == "rec"]:
            cands += ["setkey"]
        if not cands:
            return self.gen_set(indent)
        op = self.rng.choice(cands)
        # `put`/`remove` change a list's length: never target a list an
        # enclosing `for each` is iterating (termination guarantee).
        growable = [x for x in self.list_vars() if x not in self.iterated]
        if op == "put":
            if not growable:
                return self.gen_set(indent)
            n = self.rng.choice(growable)
            et = self.env[n][1]
            self._bump_len(n, 1)
            return [pad + f"put {self.gen_expr(et, 2)} in {n}"]
        if op == "remove":
            if not growable:
                return self.gen_set(indent)
            n = self.rng.choice(growable)
            et = self.env[n][1]
            self._bump_len(n, None)  # value may be absent: length unknown
            return [pad + f"remove {self.gen_expr(et, 2)} from {n}"]
        if op == "setitem":
            # index 1 is always safe on a list known non-empty at creation;
            # `remove` may have emptied it, so only target lists with a
            # tracked positive length.
            cands2 = [x for x, t in self.env.items()
                      if t[0] == "list" and len(t) > 2
                      and t[2] is not None and t[2] > 0]
            if not cands2:
                return self.gen_set(indent)
            n = self.rng.choice(cands2)
            et = self.env[n][1]
            return [pad + f"set item 1 of {n} to {self.gen_expr(et, 2)}"]
        if op == "add":
            n = self.rng.choice(
                [x for x, t in self.env.items()
                 if t == ("int",) and x not in self.protected])
            return [pad + f"add {self.gen_expr(('int',), 1)} to {n}"]
        if op == "take":
            n = self.rng.choice(
                [x for x, t in self.env.items()
                 if t == ("int",) and x not in self.protected])
            return [pad + f"take {self.gen_expr(('int',), 1)} from {n}"]
        # setkey
        n = self.rng.choice(
            [x for x, t in self.env.items() if t[0] == "rec"])
        key = self.rng.choice(list(self.env[n][1]))
        kt = self.env[n][1][key]
        return [pad + f'set {n}["{key}"] to {self.gen_expr(kt, 2)}']

    def _tiny_int_lit(self):
        # small literal safe as an argument to exponential-time (fib) or
        # huge-value (fact) functions: fib(12) is instant and 12! is still
        # exactly representable in f64 (no precision divergence).
        return str(self.rng.randint(0, 12))

    def gen_call_stmt(self):
        name = self.rng.choice(list(self.funcs))
        ptypes, ret = self.funcs[name]
        if name in self.expensive:
            args = ", ".join(self._tiny_int_lit() for _ in ptypes)
        else:
            args = ", ".join(self.gen_expr(pt, 2) for pt in ptypes)
        call = f"{name}({args})"
        if ret[0] in ("int", "text", "bool", "nothing") or (
                ret[0] == "list"):
            return f"say {call}"
        v = self.new_var()
        self.env[v] = ret
        return f"set {v} to {call}"

    # -- functions ------------------------------------------------------
    def gen_func_def(self, top=True):
        fname = self.new_func()
        style = self.rng.random()
        if style < 0.25:
            return self.gen_fact(fname)
        if style < 0.45:
            return self.gen_fib(fname)
        if style < 0.60:
            return self.gen_closure(fname)
        # plain function
        arity = self.rng.randint(0, 3)
        params = [self.new_var("p") for _ in range(arity)]
        ptypes = [("any",)] * arity
        ret = self.rng.choice([("int",), ("text", True), ("bool",),
                               ("list", ("int",)), ("nothing",)])
        saved_env = dict(self.env)
        saved_ret, self.func_ret = self.func_ret, ret
        for p in params:
            self.env[p] = ("any",)
        lines = [f"to {fname} with {', '.join(params)}:" if params
                 else f"to {fname}:"]
        lines += ["    " + l for l in
                  self.gen_block(self.rng.randint(1, 4), 0, top=False,
                                 in_loop=False, in_func=True, depth=2)]
        if ret == ("nothing",):
            pass  # falls off the end -> nothing
        else:
            lines.append(f"    give back {self.gen_expr(ret, 2)}")
        self.env = saved_env
        self.func_ret = saved_ret
        self.funcs[fname] = (ptypes, ret)
        # a call site right away keeps the function exercised
        args = ", ".join(self.gen_expr(("any",), 1) for _ in params)
        if ret == ("nothing",):
            lines.append(f"{fname}({args})")
        else:
            lines.append(f"say {fname}({args})")
        return lines

    def gen_fact(self, fname):
        lines = [f"to {fname} with n:",
                 "    if n is smaller than 2:",
                 "        give back 1",
                 "    give back n * " + f"{fname}(n - 1)"]
        self.funcs[fname] = ([("int",)], ("int",))
        self.expensive.add(fname)  # n! explodes past f64 precision; keep
                                   # every call site at small literals
        lines.append(f"say {fname}({self.rng.randint(0, 7)})")
        return lines

    def gen_fib(self, fname):
        lines = [f"to {fname} with n:",
                 "    if n is smaller than 2:",
                 "        give back n",
                 f"    give back {fname}(n - 1) + {fname}(n - 2)"]
        self.funcs[fname] = ([("int",)], ("int",))
        self.expensive.add(fname)  # exponential time; keep every call
                                   # site at small literals
        lines.append(f"say {fname}({self.rng.randint(0, 10)})")
        return lines

    def gen_closure(self, fname):
        inner = self.new_func("g")
        op = self.rng.choice(["+", "-", "*"])
        c = self.rng.randint(1, 20)
        if self.rng.random() < 0.5:
            lines = [f"to {fname} with x:",
                     f"    to {inner} with y:",
                     f"        give back x {op} y",
                     f"    give back {inner}({c})",
                     f"say {fname}({self.rng.randint(1, 20)})"]
            self.funcs[fname] = ([("int",)], ("int",))
        else:
            start = self.rng.randint(0, 50)
            v = self.new_var("cl")
            lines = [f"to {fname} with start:",
                     "    set n to start",
                     f"    to {inner}:",
                     "        set n to n + 1",
                     "        give back n",
                     f"    give back {inner}",
                     f"set {v} to {fname}({start})",
                     f"say {v}()",
                     f"say {v}()"]
            self.env[v] = ("func",)
            self.funcs[fname] = ([("int",)], ("func",))
        return lines

# ---------------------------------------------------------------------------
# expression generator (appended to the Gen class)
# ---------------------------------------------------------------------------

def _niko_str(s):
    """Render a Python string as a double-quoted Niko string literal."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') \
                 .replace("\n", "\\n").replace("\t", "\\t") + '"'


def _gen_expr(self, t, depth):
    k = t[0]
    if k == "any":
        return self._gen_expr(self.rng.choice(
            [("int",), ("text",), ("bool",), ("nothing",),
             ("list", ("int",))]), depth)
    handler = {"int": self._e_int, "float": self._e_float,
               "text": self._e_text, "bool": self._e_bool,
               "nothing": self._e_nothing, "list": self._e_list,
               "rec": self._e_rec, "result": self._e_result,
               "func": self._e_func}.get(k)
    if handler is None:
        return "nothing"
    return handler(t, depth)


def _terminal_or_var(self, t, lit):
    vs = self.vars_of_type(t)
    if vs and self.rng.random() < 0.5:
        return self.rng.choice(vs)
    return lit()


def vars_of_type(self, t):
    out = []
    for n, t2 in self.env.items():
        if t2[0] != t[0]:
            continue
        if t[0] == "list" and t2[1] != t[1]:
            continue
        out.append(n)
    return out


def _e_int(self, t, depth):
    def lit():
        return str(self.rng.choice(INT_LITS))

    if depth <= 0:
        return self._terminal_or_var(t, lit)
    r = self.rng.random()
    if r < 0.22:
        return self._terminal_or_var(t, lit)
    if r < 0.34:
        a, b = self._e_int(t, depth - 1), self._small_int(depth - 1)
        return f"{a} {self.rng.choice(['+', '-', '*'])} {b}"
    if r < 0.40:
        a = self._e_int(t, depth - 1)
        b = self.rng.choice([2, 3, 5, 7, 10])
        return f"{a} % {b}"
    if r < 0.44:
        return f"{self.rng.randint(0, 5)} ** {self.rng.randint(0, 5)}"
    if r < 0.50:
        return f"-{self._e_int(t, depth - 1)}"
    if r < 0.56:
        return f"length({self._gen_expr(self.rng.choice([('text',), ('list', ('int',)), ('list', ('text',))]), depth - 1)})"
    if r < 0.62:
        return f"{self.rng.choice(['round', 'ceil', 'floor'])}({self._e_float(('float',), depth - 1)})"
    if r < 0.66:
        return f"abs({self._e_int(t, depth - 1)})"
    if r < 0.70:
        a, b = self._e_int(t, depth - 1), self._e_int(t, depth - 1)
        return f"{self.rng.choice(['min', 'max'])}({a}, {b})"
    if r < 0.74:
        return f"sum({self._list_lit(('int',), 1, 4, depth - 1)})"
    if r < 0.78:
        sub = _niko_str(self.rng.choice(["l", "o", "a", "e", "x"]))
        return f"count_of({self._e_text(("text",), depth - 1)}, {sub})"
    if r < 0.82:
        return f"number({_niko_str(str(self.rng.randint(-9999, 9999)))})"
    if r < 0.86:
        return self._index_expr(("int",), depth)
    if r < 0.90:
        # type-consistent: ok carries an int and the default is an int,
        # so the value is always an int at runtime (never a mixed-type
        # surprise for the backends).
        d = self.rng.randint(0, 99)
        v = self._e_int(t, 0)
        if self.rng.random() < 0.7:
            return f"unwrap_or(ok({v}), {d})"
        return (f"unwrap_or(error("
                f"{_niko_str(self.rng.choice(['boom', 'oops']))}), {d})")
    if r < 0.94:
        int_funcs = [n for n, (_, ret) in self.funcs.items()
                     if ret == ("int",)]
        if int_funcs:
            return self._call_expr(self.rng.choice(int_funcs))
        return self._terminal_or_var(t, lit)
    return f"({self._e_int(t, depth - 1)})"


def _small_int(self, depth):
    if depth <= 0 or self.rng.random() < 0.6:
        return str(self.rng.randint(-20, 20))
    return self._e_int(("int",), depth)


def _e_float(self, t, depth):
    def lit():
        return str(self.rng.choice(FLOAT_LITS))

    if depth <= 0:
        return self._terminal_or_var(t, lit)
    r = self.rng.random()
    if r < 0.25:
        return self._terminal_or_var(t, lit)
    if r < 0.45:
        a = self._gen_expr(self.rng.choice([("int",), ("float",)]),
                           depth - 1)
        b = self._gen_expr(self.rng.choice([("int",), ("float",)]),
                           depth - 1)
        return f"{a} {self.rng.choice(['+', '-', '*'])} {b}"
    if r < 0.55:
        a = self._gen_expr(self.rng.choice([("int",), ("float",)]),
                           depth - 1)
        b = self.rng.choice([x for x in FLOAT_LITS if x != 0] +
                            [2, 4, 5])
        return f"{a} / {b}"
    if r < 0.62:
        return f"-{self._e_float(t, depth - 1)}"
    if r < 0.70:
        x = self._gen_expr(self.rng.choice([("int",), ("float",)]),
                           depth - 1)
        return f"sqrt(abs({x}))"
    if r < 0.78:
        return f"average({self._list_lit(self.rng.choice([('int',), ('float',)]), 1, 4, depth - 1)})"
    if r < 0.86:
        return f"number({_niko_str(self.rng.choice(['3.5', '-2.25', '0.5', '10.75']))})"
    return f"({self._e_float(t, depth - 1)})"


def _e_text(self, t, depth):
    def lit():
        pool = self.rng.random()
        if pool < 0.6:
            return _niko_str(self.rng.choice(ASCII_WORDS))
        if pool < 0.85:
            return _niko_str(self.rng.choice(UNI_WORDS))
        return self.rng.choice(ESCAPED_TEXTS)

    if depth <= 0:
        return self._terminal_or_var(t, lit)
    r = self.rng.random()
    if r < 0.25:
        return self._terminal_or_var(t, lit)
    if r < 0.38:
        # anchor one side with a literal: the checker infers any+any as
        # number, so a bare `f(x) + g(x)` would check as number, not text
        # (breaks match-arm type uniformity).
        return f"{lit()} + {self._e_text(t, depth - 1)}"
    if r < 0.46:
        return f"{self.rng.choice(['upper', 'lower'])}({_niko_str(self.rng.choice(ASCII_WORDS))})"
    if r < 0.52:
        return f"trim({self._e_text(t, depth - 1)})"
    if r < 0.58:
        a = _niko_str(self.rng.choice(["a", "e", "l", "o", " "]))
        b = _niko_str(self.rng.choice(["b", "x", "-", "_"]))
        return f"replace({self._e_text(t, depth - 1)}, {a}, {b})"
    if r < 0.64:
        return f"text({self._e_int(("int",), depth - 1)})"
    if r < 0.70:
        return self._text_index(depth)
    if r < 0.76:
        return f'error_message(error({_niko_str(self.rng.choice(["boom", "oops", "bad"]))}))'
    return f"({self._e_text(t, depth - 1)})"


def _text_index(self, depth):
    # index into a text literal (length known) — 1-based item_of or
    # 0-based [i]; multibyte-safe by construction
    s = self.rng.choice(ASCII_WORDS + UNI_WORDS)
    chars = list(s)
    if not chars:
        return _niko_str("")
    i = self.rng.randrange(len(chars))
    lit = _niko_str(s)
    if self.rng.random() < 0.5:
        return f"item_of({i + 1}, {lit})"
    return f"{lit}[{i}]"


def _e_bool(self, t, depth):
    if depth <= 0 or self.rng.random() < 0.2:
        vs = self.vars_of_type(t)
        if vs and self.rng.random() < 0.5:
            return self.rng.choice(vs)
        return self.rng.choice(["yes", "no"])
    r = self.rng.random()
    if r < 0.35:
        return self._gen_cmp(depth)
    if r < 0.45:
        a = self._gen_operand(depth - 1)
        return f"{a} is in {self._gen_membership_target(a, depth - 1)}"
    if r < 0.60:
        a, b = self._gen_operand(depth - 1), self._gen_operand(depth - 1)
        return f"{a} {self.rng.choice(['and', 'or'])} {b}"
    if r < 0.68:
        return f"not ({self._e_bool(t, depth - 1)})"
    if r < 0.76:
        return f"{self.rng.choice(['is_ok', 'is_error'])}({self._e_result(t, depth - 1)})"
    if r < 0.84:
        c = self._e_text(("text",), depth - 1)
        x = _niko_str(self.rng.choice(["a", "e", "h", "o", "ll"]))
        return f"{self.rng.choice(['starts_with', 'ends_with'])}({c}, {x})"
    if r < 0.90:
        c = self._gen_expr(self.rng.choice(
            [("text",), ("list", ("int",)), ("rec", {"k0": ("int",)})]),
            depth - 1)
        return f"has({c}, {self._has_needle(c)})"
    bool_funcs = [n for n, (_, ret) in self.funcs.items()
                  if ret == ("bool",)]
    if r < 0.95 and bool_funcs:
        return self._call_expr(self.rng.choice(bool_funcs))
    return self._gen_cmp(depth)


def _gen_operand(self, depth):
    # operand for and/or: any type (operand-returning semantics)
    t = self.rng.choice([("int",), ("text",), ("bool",), ("nothing",),
                         ("list", ("int",))])
    e = self._gen_expr(t, depth)
    # parenthesize comparisons inside and/or chains
    if t == ("bool",) and any(op in e for op in CMP_OPS):
        return f"({e})"
    return e


def _gen_cmp(self, depth):
    dom = self.rng.random()
    if dom < 0.5:
        a = self._gen_expr(self.rng.choice([("int",), ("float",)]),
                           depth - 1)
        b = self._gen_expr(self.rng.choice([("int",), ("float",)]),
                           depth - 1)
        return f"{a} {self.rng.choice(CMP_OPS)} {b}"
    if dom < 0.75:
        a, b = self._e_text(("text",), depth - 1), self._e_text(("text",), depth - 1)
        return f"{a} {self.rng.choice(['is', 'is not', 'is smaller than', 'is bigger than', '==', '!='])} {b}"
    if dom < 0.9:
        a = self.rng.choice(["yes", "no"])
        b = self.rng.choice(["yes", "no"])
        return f"{a} {self.rng.choice(['is', 'is not', '==', '!='])} {b}"
    a = self.rng.choice(["nothing"])
    return f"{a} {self.rng.choice(['is', 'is not'])} nothing"


def _gen_membership_target(self, a, depth):
    # `a is in TARGET`
    if self.rng.random() < 0.5:
        return self._list_lit(("int",), 1, 4, depth)
    return self._e_text(("text",), depth)


def _has_needle(self, c):
    return _niko_str(self.rng.choice(["a", "k0", "1", "e"]))


def _e_nothing(self, t, depth):
    vs = self.vars_of_type(t)
    if vs and self.rng.random() < 0.4:
        return self.rng.choice(vs)
    return "nothing"


def _list_lit(self, elem, lo, hi, depth):
    n = self.rng.randint(lo, hi)
    return "[" + ", ".join(self._gen_expr(elem, depth) for _ in range(n)) + "]"


def _e_list(self, t, depth):
    elem = t[1] if len(t) > 1 else ("any",)
    if depth <= 0:
        vs = self.vars_of_type(("list", elem))
        if vs and self.rng.random() < 0.5:
            return self.rng.choice(vs)
        return self._list_lit(elem, 0, 3, 0)
    r = self.rng.random()
    if r < 0.35:
        vs = self.vars_of_type(("list", elem))
        if vs and self.rng.random() < 0.5:
            return self.rng.choice(vs)
        return self._list_lit(elem, 0, 4, depth - 1)
    if r < 0.50:
        base = self._e_list(t, 0)
        return f"{self.rng.choice(['sorted', 'unique', 'reversed'])}({base})"
    if r < 0.60 and elem == ("text",):
        return f"split({self._e_text(("text",), depth - 1)}, {_niko_str(self.rng.choice([',', ' ', '-']))})"
    if r < 0.68 and elem == ("int",):
        a = self.rng.randint(-5, 5)
        b = self.rng.randint(-5, 5)
        return f"numbers {a} to {b}"
    if r < 0.76:
        return self._index_expr(("list", elem), depth)
    return self._list_lit(elem, 0, 4, depth - 1)


def _index_expr(self, want_t, depth):
    """index_of / [i] into a list of known length (literal or tracked var)"""
    if self.rng.random() < 0.5 or True:
        # inline literal: length known
        elem = want_t[1] if want_t[0] == "list" else want_t
        n = self.rng.randint(1, 4)
        elems = [self._gen_expr(elem, 0) for _ in range(n)]
        lit = "[" + ", ".join(elems) + "]"
        i = self.rng.randrange(n)
        if self.rng.random() < 0.5:
            e = f"item_of({i + 1}, {lit})"
        else:
            e = f"{lit}[{i}]"
        return e if want_t[0] != "list" else lit
    # (var-indexing kept out: lengths go stale after mutation)


def _e_rec(self, t, depth):
    if depth <= 0:
        vs = self.vars_of_type(t)
        if vs and self.rng.random() < 0.5:
            return self.rng.choice(vs)
    schema = t[1] if len(t) > 1 and t[1] else self.gen_rec_schema()
    parts = [f"{k}: {self._gen_expr(vt, max(depth - 1, 0))}"
             for k, vt in schema.items()]
    return "{" + ", ".join(parts) + "}"


def _e_result(self, t, depth):
    r = self.rng.random()
    if r < 0.4:
        return f"ok({self._gen_expr(self.rng.choice([('int',), ('text',)]), 0)})"
    if r < 0.6:
        return f"error({_niko_str(self.rng.choice(['boom', 'oops', 'bad input']))})"
    # try_number: valid input -> ok. Invalid inputs are NOT generated:
    # the text-failure error message diverges across backends
    # (KNOWN_LIMITATIONS: VM builds "I can't turn 'x' into a number.",
    # WASM/native emit the bare input), and the message is observable
    # both directly and through string ops on the bound error, so no
    # output normalization can allowlist every derived form.
    return f"try_number({_niko_str(str(self.rng.randint(-999, 9999)))})"


def _e_func(self, t, depth):
    vs = self.vars_of_type(t)
    if vs:
        return self.rng.choice(vs)
    return "nothing"


def _call_expr(self, fname):
    ptypes, _ret = self.funcs[fname]
    if fname in self.expensive:
        args = ", ".join(self._tiny_int_lit() for _ in ptypes)
    else:
        # depth 0: terminals only, so calls cannot nest inside expressions
        # (geometric nesting would make generator termination merely
        # almost-sure instead of guaranteed).
        args = ", ".join(self._gen_expr(pt, 0) for pt in ptypes)
    return f"{fname}({args})"


# bind the helpers onto Gen
for _name in ["_gen_expr", "_terminal_or_var", "vars_of_type", "_e_int",
              "_small_int", "_e_float", "_e_text", "_text_index", "_e_bool",
              "_gen_operand", "_gen_cmp", "_gen_membership_target",
              "_has_needle", "_e_nothing", "_list_lit", "_e_list",
              "_index_expr", "_e_rec", "_e_result", "_e_func", "_call_expr"]:
    setattr(Gen, _name, globals()[_name])
del _name
# public alias used by the statement generators
Gen.gen_expr = Gen._gen_expr

# ---------------------------------------------------------------------------
# differential runner
# ---------------------------------------------------------------------------

import re as _re

BACKENDS = ("vm", "wasm", "native")

_FLOAT_RENDER_RE = _re.compile(rb"(?<![\d.])(\d+)\.0(?![\d.])")


def _float_render_norm(out):
    """Normalize the documented integral-float rendering split (VM prints
    ``text(2.0)`` as ``2.0``; WASM/native print ``2`` — Alpha 31 known
    limit). Used ONLY to recognize that known divergence, never to hide
    anything else."""
    return _FLOAT_RENDER_RE.sub(rb"\1", out)


def _try_number_norm(out):
    # Collapse the known try_number text-failure message divergence
    # (KNOWN_LIMITATIONS, Alpha 36): the VM says
    #   I can't turn '<input>' into a number.
    # while WASM/native emit the bare <input>. Normalize the VM form to
    # the bare input so outputs that differ only in this wrapper compare
    # equal. Documented approximation: a program that prints the wrapper
    # sentence as a *literal* string would also normalize (the generator
    # has no reason to emit that exact sentence).
    import re
    return re.sub(rb"I can't turn '(.*?)' into a number\.",
                  rb"\1", out)


EXPECTED_DIVERGENCES = {
    # name -> predicate(outs: dict[backend, bytes]) -> True if the mismatch
    # is exactly this documented divergence.
    "float-render": lambda outs: len(
        {_float_render_norm(o) for o in outs.values()}) == 1,
    "try-number-message": lambda outs: len(
        {_try_number_norm(o) for o in outs.values()}) == 1,
}


def strip_banner(out):
    """Drop the CLI's `✓ built ...` first line (wasm/native --run)."""
    if out.startswith("✓ built".encode("utf8")):
        _, _, rest = out.partition(b"\n")
        return rest
    lines = out.split(b"\n", 1)
    if lines[0].decode("utf8", "replace").startswith("✓ built"):
        return lines[1] if len(lines) > 1 else b""
    return out


def run_backend(backend, src_path, stdin_text, timeout):
    """Run one backend as a subprocess. Returns (rc, stdout_bytes) where
    rc is an int, or the string 'timeout' when the backend hangs."""
    if backend == "vm":
        cmd = [sys.executable, "-m", "niko2", "run", str(src_path)]
    elif backend == "wasm":
        cmd = [sys.executable, "-m", "niko2", "wasm", str(src_path), "--run"]
    elif backend == "native":
        cmd = [sys.executable, "-m", "niko2", "native", str(src_path),
               "--run"]
    else:
        raise ValueError(f"unknown backend {backend!r}")
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
    data = stdin_text.encode("utf8") if stdin_text else None
    try:
        r = subprocess.run(cmd, capture_output=True, cwd=str(REPO_ROOT),
                           env=env, input=data, timeout=timeout)
    except subprocess.TimeoutExpired:
        return ("timeout", b"")
    out = r.stdout
    if backend in ("wasm", "native"):
        out = strip_banner(out)
    return (r.returncode, out)


def classify(results):
    """Classify a per-backend result dict.

    Returns (kind, detail) where kind is one of:
      'pass'        every backend agrees (rc 0 + identical stdout), or every
                    backend failed with the same rc (error text differs by
                    design across backends)
      'known'       mismatch matches an EXPECTED_DIVERGENCES entry (detail =
                    divergence name)
      'fail'        real divergence (detail = short reason)
    A hung backend ('timeout') is always a failure.
    """
    rcs = {b: r[0] for b, r in results.items()}
    outs = {b: r[1] for b, r in results.items()}
    if any(rc == "timeout" for rc in rcs.values()):
        hung = sorted(b for b, rc in rcs.items() if rc == "timeout")
        return ("fail", f"backend hang (timeout): {', '.join(hung)}")
    if any(rc != 0 for rc in rcs.values()):
        if all(rc != 0 for rc in rcs.values()):
            if len(set(rcs.values())) == 1:
                return ("pass", "error-agree")
            return ("fail",
                    f"all backends errored but returncodes differ: {rcs}")
        bad = sorted(f"{b}(rc={rcs[b]})" for b in rcs if rcs[b] != 0)
        return ("fail", f"backend error while others succeeded: "
                        f"{', '.join(bad)}")
    # every backend rc == 0
    if len(set(outs.values())) == 1:
        return ("pass", "identical")
    for name, pred in EXPECTED_DIVERGENCES.items():
        try:
            if pred(outs):
                return ("known", name)
        except Exception:
            pass
    return ("fail", "stdout mismatch")


# ---------------------------------------------------------------------------
# minimizer (greedy line deletion)
# ---------------------------------------------------------------------------

def _signature(src, stdin_text, backends, timeout, workdir):
    """Backend signature of a program: per-backend (rc, stdout)."""
    p = workdir / "min.niko"
    p.write_bytes(src.encode("utf8"))
    return {b: run_backend(b, p, stdin_text, timeout) for b in backends}


def minimize(src, stdin_text, backends, timeout, orig_results):
    """Greedily delete lines while the program still parses/checks AND
    still fails with the identical backend signature.

    Limits (by design): line-granular (cannot shrink inside a line),
    greedy (local minima possible — e.g. two lines that must go together
    are never removed), assumes determinism, and costs O(lines) backend
    runs per pass. Runs at most 3 passes.
    """
    workdir = Path("fuzz_work")
    workdir.mkdir(exist_ok=True)
    # minimize over the reference backend + the ones that disagreed with it
    ref = "vm" if "vm" in backends else backends[0]
    ref_out = orig_results[ref]
    min_backends = [ref] + [b for b in backends
                            if b != ref and orig_results[b] != ref_out]
    sig0 = {b: orig_results[b] for b in min_backends}

    def still_fails(text):
        if not valid_program(text):
            return False
        sig = _signature(text, stdin_text, min_backends, timeout, workdir)
        if any(v[0] == "timeout" for v in sig.values()):
            return False
        if sig != sig0:
            return False
        return classify(sig)[0] == "fail"

    lines = src.splitlines()
    for _pass in range(3):
        changed = False
        i = 0
        while i < len(lines):
            cand = lines[:i] + lines[i + 1:]
            if cand and still_fails("\n".join(cand) + "\n"):
                lines = cand
                changed = True
                # don't advance i: try deleting the next line too
            else:
                i += 1
        if not changed:
            break
    # clean up the scratch dir
    try:
        for f in workdir.iterdir():
            f.unlink()
        workdir.rmdir()
    except OSError:
        pass
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# failure / corpus saving
# ---------------------------------------------------------------------------

def save_case(directory, name, src, stdin_text, results, report):
    d = Path(directory)
    d.mkdir(exist_ok=True)
    (d / f"{name}.niko").write_text(src, encoding="utf8")
    if stdin_text:
        (d / f"{name}.stdin.txt").write_text(stdin_text, encoding="utf8")
    if report is not None:
        (d / f"{name}.report.txt").write_text(report, encoding="utf8")


def failure_report(seed, case_no, backends, results, kind_detail):
    out = [f"niko2 fuzz failure — seed {seed} case {case_no}",
           f"backends: {', '.join(backends)}",
           f"classification: {kind_detail[0]} ({kind_detail[1]})", ""]
    for b in backends:
        rc, ob = results[b]
        out.append(f"--- {b}: rc={rc}")
        try:
            txt = ob.decode("utf8", "replace")
        except Exception:
            txt = repr(ob)
        if len(txt) > 2000:
            txt = txt[:2000] + f"\n... ({len(txt)} chars total)"
        out.append(txt if txt else "(no output)")
    # pairwise diff of stdout, first 60 lines
    bs = list(backends)
    for i in range(len(bs)):
        for j in range(i + 1, len(bs)):
            a, b = bs[i], bs[j]
            if results[a][1] != results[b][1]:
                out.append(f"--- diff {a} vs {b} (first 60 lines):")
                da = results[a][1].decode("utf8", "replace").splitlines()
                db = results[b][1].decode("utf8", "replace").splitlines()
                diff = list(difflib.unified_diff(
                    da, db, fromfile=a, tofile=b, lineterm=""))
                out.extend(diff[:60])
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# campaign driver
# ---------------------------------------------------------------------------

def _have(cmd):
    import shutil
    return shutil.which(cmd) is not None


def generate_valid(gen, max_attempts=60):
    """Generate programs until one parses+checks (or attempts run out)."""
    for _ in range(max_attempts):
        src, stdin_text = gen.gen_program()
        if valid_program(src):
            return src, stdin_text, True
    return src, stdin_text, False


def run_cases(backends, cases, seed, timeout, native_sample, keep_passing,
              minimize_on, progress_every=50, if_bias=0):
    import tempfile
    gen = Gen(seed, if_bias=if_bias)
    tally = {"pass": 0, "known": 0, "fail": 0, "reject": 0,
             "known_names": {}}
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        try:
            for i in range(cases):
                src, stdin_text, ok = generate_valid(gen)
                if not ok:
                    tally["reject"] += 1
                    continue
                p = tmp / "case.niko"
                p.write_text(src, encoding="utf8")
                run_bs = [b for b in backends
                          if not (b == "native" and native_sample > 1
                                  and i % native_sample != 0)]
                results = {b: run_backend(b, p, stdin_text, timeout)
                           for b in run_bs}
                kind, detail = classify(results)
                if kind == "pass":
                    tally["pass"] += 1
                    if keep_passing:
                        save_case("fuzz_corpus", f"fuzz_{seed}_{i}",
                                  src, stdin_text, results, None)
                elif kind == "known":
                    tally["known"] += 1
                    tally["known_names"][detail] = \
                        tally["known_names"].get(detail, 0) + 1
                else:
                    tally["fail"] += 1
                    name = f"fuzz_{seed}_{i}"
                    if minimize_on:
                        msrc = minimize(src, stdin_text, run_bs, timeout,
                                        results)
                        # re-verify the minimized program with a fresh run
                        mp = tmp / "mincheck.niko"
                        mp.write_text(msrc, encoding="utf8")
                        mresults = {b: run_backend(b, mp, stdin_text,
                                                   timeout)
                                    for b in run_bs}
                        mkind, mdetail = classify(mresults)
                        src, results = msrc, mresults
                        note = (f"minimized ({len(msrc.splitlines())} lines, "
                                f"re-verified: {mkind}/{mdetail})")
                    else:
                        note = "minimizer disabled"
                    report = failure_report(seed, i, run_bs, results,
                                            (kind, detail))
                    report += f"\nnote: {note}\n"
                    save_case("fuzz_failures", name, src, stdin_text,
                              results, report)
                    failures.append((i, kind, detail))
                    print(f"FAIL case {i} [{kind}/{detail}] "
                          f"-> fuzz_failures/{name}.niko")
                if (i + 1) % progress_every == 0:
                    print(f"[{i + 1}/{cases}] pass={tally['pass']} "
                          f"known={tally['known']} fail={tally['fail']} "
                          f"reject={tally['reject']}")
        except KeyboardInterrupt:
            print(f"\ninterrupted at case {i + 1}/{cases}")
    return tally, failures


def run_corpus(corpus_dir, backends, timeout):
    d = Path(corpus_dir)
    files = sorted(d.glob("*.niko"))
    if not files:
        print(f"corpus: no *.niko in {d}")
        return 2
    import tempfile
    fails = 0
    with tempfile.TemporaryDirectory() as tmp:
        for p in files:
            src = p.read_text(encoding="utf8")
            sp = p.with_suffix(".stdin.txt")
            stdin_text = sp.read_text(encoding="utf8") if sp.exists() else ""
            tp = Path(tmp) / "c.niko"
            tp.write_text(src, encoding="utf8")
            results = {b: run_backend(b, tp, stdin_text, timeout)
                       for b in backends}
            kind, detail = classify(results)
            status = "ok" if kind == "pass" else kind.upper()
            print(f"{status:5} {p.name} [{detail}]")
            if kind == "fail":
                fails += 1
    print(f"corpus: {len(files) - fails}/{len(files)} agree")
    return 1 if fails else 0


def build_arg_parser():
    ap = argparse.ArgumentParser(prog="niko2 fuzz",
                                 description="differential fuzzer: VM vs "
                                             "WASM vs native")
    ap.add_argument("--seed", type=int, default=None,
                    help="RNG seed (default: random; always printed)")
    ap.add_argument("--cases", type=int, default=200,
                    help="programs to generate (default 200)")
    ap.add_argument("--backend", default="vm,wasm,native",
                    help="comma-separated subset of vm,wasm,native")
    ap.add_argument("--timeout", type=float, default=10,
                    help="per-backend seconds before a hang is a failure")
    ap.add_argument("--native-sample", type=int, default=1,
                    help="run native only on every Nth case (default 1)")
    ap.add_argument("--keep-passing", action="store_true",
                    help="save passing programs to fuzz_corpus/")
    ap.add_argument("--no-minimize", action="store_true",
                    help="skip the shrink pass on failures")
    ap.add_argument("--corpus", default=None, metavar="DIR",
                    help="re-run saved *.niko cases instead of generating")
    return ap


def main(a):
    """Entry point wired from niko2/cli.py (`niko2 fuzz ...`). `a` is the
    top-level argparse namespace."""
    backends = [b.strip() for b in a.backend.split(",") if b.strip()]
    for b in backends:
        if b not in BACKENDS:
            print(f"niko2 fuzz: unknown backend {b!r} "
                  f"(choose from vm,wasm,native)")
            return 2
    if "wasm" in backends and not _have("node"):
        print("niko2 fuzz: node.js not on PATH, skipping wasm backend")
        backends = [b for b in backends if b != "wasm"]
    cc = _have("cc") or _have("gcc") or _have("clang")
    if "native" in backends and not cc:
        print("niko2 fuzz: no C compiler on PATH, skipping native backend")
        backends = [b for b in backends if b != "native"]
    if not backends:
        print("niko2 fuzz: no backends left to run")
        return 2

    seed = a.seed
    if seed is None:
        seed = random.SystemRandom().randint(0, 2 ** 31 - 1)
    print(f"niko2 fuzz: seed {seed}")

    if a.corpus:
        return run_corpus(a.corpus, backends, a.timeout)

    if a.cases is not None and a.cases < 1:
        print("niko2 fuzz: --cases must be >= 1")
        return 2
    cases = a.cases or 200
    ns = " (native sampled)" if (a.native_sample > 1
                                 and "native" in backends) else ""
    print(f"niko2 fuzz: {cases} cases on {', '.join(backends)}{ns}, "
          f"timeout {a.timeout}s")
    tally, failures = run_cases(backends, cases, seed, a.timeout,
                                a.native_sample, a.keep_passing,
                                not a.no_minimize, if_bias=a.if_bias)
    print(f"niko2 fuzz: done seed={seed} cases={cases} "
          f"pass={tally['pass']} known-divergence={tally['known']} "
          f"{tally['known_names']} fail={tally['fail']} "
          f"generator-reject={tally['reject']}")
    if failures:
        print(f"niko2 fuzz: {len(failures)} divergence(s); see "
              f"fuzz_failures/ and re-run with --corpus fuzz_failures")
        return 1
    return 0
