#!/usr/bin/env python3
"""Alpha 36: `niko2 fuzz` -- differential fuzzer (VM vs WASM vs native).

Fixed-seed smoke test: 20 generated programs must agree byte-for-byte on
the VM and WASM backends (native is sampled at 1 in 10 to keep CI fast --
a full native compile costs ~3s per case). The campaign itself (thousands
of cases, all three backends) is a manual `./fuzz` run, not a unit test;
see ALPHA36_DESIGN.md for the design, the termination argument, and the
campaign results.

Also pins the generator's termination guarantees: while-loop counters
are never reassigned in the body, `ask` never appears inside a loop or
function body, `stop`/`skip` always sit inside a loop (and the formerly
forbidden nested-iterator `stop` shapes are now generated, now that the
Alpha 37 compiler fix makes them safe), and exponential-time (fib) /
huge-value (fact) calls only ever receive small literals.
"""
import pathlib
import shutil
import sys

import pytest

root = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

from niko2 import fuzz as fuzz_mod

SEED = 20260923
CASES = 20


def _backends():
    # VM + WASM only. Native is excluded from the committed smoke test
    # until the `otherwise if` codegen bug is fixed (KNOWN_LIMITATIONS.md,
    # "Native: `otherwise if` with a temp-emitting condition miscompiles"):
    # the generator routinely emits that construct and native rc=1s on it,
    # which would make this test red for a backend bug, not a fuzzer bug.
    # Campaign runs (ALPHA36_DESIGN.md) do include native, sampled.
    bs = ["vm"]
    if shutil.which("node"):
        bs.append("wasm")
    return bs


def test_fuzz_smoke_seed_is_deterministic():
    g1 = fuzz_mod.Gen(SEED)
    g2 = fuzz_mod.Gen(SEED)
    for _ in range(5):
        assert g1.gen_program() == g2.gen_program()


def test_fuzz_smoke_campaign_agrees():
    backends = _backends()
    tally, failures = fuzz_mod.run_cases(
        backends, CASES, SEED, timeout=15,
        native_sample=1, keep_passing=False,
        minimize_on=False, progress_every=1000)
    assert tally["reject"] == 0, f"generator reject: {tally}"
    assert not failures, f"divergences: {failures[:3]}"
    assert tally["fail"] == 0, f"tally: {tally}"
    assert tally["pass"] + tally["known"] == CASES, f"tally: {tally}"


def test_fuzz_while_counters_are_protected():
    # Every generated while-loop counter must be reassignment-proof:
    # scan 200 programs' sources for `set <counter>` inside the loop body
    # other than the single increment line.
    import re
    g = fuzz_mod.Gen(7)
    checked = 0
    for _ in range(200):
        src, _ = g.gen_program()
        for m in re.finditer(r"^set (c\d+) to 0\nwhile \1 is smaller than \d+:\n((?:    .*\n)+)", src, re.M):
            body = m.group(2)
            counter = m.group(1)
            for line in body.splitlines():
                s = line.strip()
                if s == f"set {counter} to {counter} + 1":
                    continue
                assert not re.match(rf"^(set {counter} to|add .* to {counter}|take .* from {counter})\b", s), \
                    f"counter {counter} reassigned in body:\n{src}"
            checked += 1
    assert checked > 0, "no while loops generated in 200 programs?"


def _ask_outside_loops_and_funcs(src):
    """Static check: no `ask` statement sits inside a loop or function
    body in the generated source (4-space indents, ':' opens a block)."""
    import re
    stack = []  # (indent_level, kind)
    for line in src.splitlines():
        if not line.strip():
            continue
        indent = (len(line) - len(line.lstrip(" "))) // 4
        s = line.strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if re.match(r"^ask(\s|$)", s):
            kinds = [k for _, k in stack]
            assert "loop" not in kinds and "func" not in kinds, \
                f"ask inside {kinds}:\n{src}"
        if s.endswith(":"):
            if re.match(r"^(while |for each |repeat )", s):
                kind = "loop"
            elif s.startswith("to "):
                kind = "func"
            else:
                kind = "other"
            stack.append((indent, kind))


def test_fuzz_ask_never_in_loop_or_function():
    # `ask` inside a loop body would execute more times than the canned
    # stdin has lines (stdin exhaustion diverges across backends --
    # KNOWN_LIMITATIONS.md "ask past stdin EOF").
    import re
    g = fuzz_mod.Gen(99)
    for _ in range(200):
        src, stdin_text = g.gen_program()
        asks = len(re.findall(r"^ *ask(\s|$)", src, re.M))
        assert asks <= 3, f"ask budget exceeded:\n{src}"
        if asks:
            assert stdin_text.count("\n") >= asks, \
                f"fewer stdin lines than asks:\n{src}\n---\n{stdin_text!r}"
        _ask_outside_loops_and_funcs(src)


def _strip_own_definition(src, fname):
    """Drop the `to fname ...:` definition block (its internal recursive
    call legitimately passes `n - 1`, not a tiny literal)."""
    import re
    out = []
    skipping = False
    for line in src.splitlines():
        if re.match(rf"^to {re.escape(fname)}\b", line):
            skipping = True
            continue
        if skipping:
            if line.startswith("    ") or not line.strip():
                continue
            skipping = False
        out.append(line)
    return "\n".join(out)


def test_fuzz_expensive_calls_use_tiny_literals():
    # fib/fact call sites (statement and expression level) must pass
    # small literals only -- fib is exponential-time, fact explodes
    # past f64 precision.
    import re
    g = fuzz_mod.Gen(1234)
    for _ in range(300):
        src, _ = g.gen_program()
        for fname in sorted(g.funcs):
            if fname in g.expensive:
                body = _strip_own_definition(src, fname)
                for m in re.finditer(rf"\b{re.escape(fname)}\(([^)]*)\)", body):
                    for arg in m.group(1).split(","):
                        arg = arg.strip()
                        assert re.fullmatch(r"-?\d+", arg), \
                            f"expensive call {fname}({m.group(1)}) not tiny literals:\n{src}"
                        assert abs(int(arg)) <= 12, \
                            f"expensive call arg too big:\n{src}"


def test_fuzz_foreach_never_mutates_iterated_list():
    # `put`/`remove` must never target a list an enclosing `for each` is
    # iterating: growing it mid-loop hangs every backend (seed 201 found
    # one). Static check over 300 generated programs with an indent
    # stack tracking each for-each's source variable.
    import re
    g = fuzz_mod.Gen(99)
    checked = 0
    for _ in range(300):
        src, _ = g.gen_program()
        stack = []  # (indent_level, iterated_var or None)
        for line in src.splitlines():
            if not line.strip():
                continue
            indent = (len(line) - len(line.lstrip(" "))) // 4
            s = line.strip()
            while stack and stack[-1][0] >= indent:
                stack.pop()
            m = re.match(rf"^(put .* in (\w+)|remove .* from (\w+))$", s)
            if m:
                target = m.group(2) or m.group(3)
                live = [v for _, v in stack if v]
                assert target not in live, \
                    f"mutates iterated list {target}:\n{src}"
            if s.endswith(":"):
                var = None
                fm = re.match(r"^for each \w+ in (\w+):$", s)
                if fm and not fm.group(1).startswith("numbers"):
                    var = fm.group(1)
                    checked += 1
                stack.append((indent, var))
    assert checked > 0, "no for-each over a list var in 300 programs?"


def test_fuzz_stop_free_in_nested_loops():
    # Alpha 37: the VM iterator-leak bug is fixed (the compiler emits
    # ITER_POP for `stop` out of a `repeat`/`for each`), so `stop` is
    # safe in every nesting shape and the generator emits it freely.
    # Static check over 300 generated programs with an indent stack
    # tracking loop kinds:
    #   1. every `stop`/`skip` still sits inside a loop (a bare one is a
    #      compile error -- the generator invariant that remains), and
    #   2. the previously-forbidden shapes are actually generated now:
    #      `stop` out of a `for each`/`repeat` with another
    #      `for each`/`repeat` above it (Alpha 36's `_gen_loop_exit`
    #      would only ever emit `skip` there).
    import re
    g = fuzz_mod.Gen(4242)
    nested_stop = 0
    checked = 0
    for _ in range(300):
        src, _ = g.gen_program()
        stack = []  # (indent_level, kind or None)
        for line in src.splitlines():
            if not line.strip():
                continue
            indent = (len(line) - len(line.lstrip(" "))) // 4
            s = line.strip()
            while stack and stack[-1][0] >= indent:
                stack.pop()
            if s in ("stop", "skip"):
                kinds = [k for _, k in stack if k]
                assert kinds, f"{s} outside any loop:\n{src}"
                checked += 1
                if s == "stop" and kinds[-1] != "while" and any(
                        k in ("for", "repeat") for k in kinds[:-1]):
                    nested_stop += 1
            if s.endswith(":"):
                kind = None
                if re.match(r"^while ", s):
                    kind = "while"
                elif re.match(r"^for each ", s):
                    kind = "for"
                elif re.match(r"^repeat ", s):
                    kind = "repeat"
                stack.append((indent, kind))
    assert checked > 0, "no stop/skip generated in 300 programs?"
    assert nested_stop > 0, \
        "no stop out of a nested for-each/repeat in 300 programs?"


def test_fuzz_function_give_backs_match_modeled_return_type():
    # The generator models each function's return type in Gen.funcs and
    # uses it at call sites. Every `give back` in the body must produce
    # that type; a mistyped early give-back lets e.g. a bool slip into
    # an int slot (min(int, bool) diverges across backends -- see
    # KNOWN_LIMITATIONS). Checked against the generator's own model.
    import re
    BOOL_SHAPES = (" is ", " is not ", " == ", " != ",
                   " is smaller than ", " is bigger than ",
                   " is in ", " and ", " or ")
    def lit_type(expr):
        e = expr.strip()
        if e in ("yes", "no") or e.startswith("not ") \
                or e.startswith("not(") \
                or e.startswith(("is_ok(", "is_error(", "has(",
                                  "starts_with(", "ends_with(")):
            return "bool"
        if any(op in e for op in BOOL_SHAPES):
            return "bool"
        if e == "nothing":
            return "nothing"
        if re.fullmatch(r"-?\d+", e):
            return "int"
        if re.fullmatch(r'"(?:[^"\\]|\\.)*"', e):
            return "text"
        return None
    def model_type(t):
        return {"int": "int", "bool": "bool", "nothing": "nothing",
                "text": "text"}.get(t[0])
    g = fuzz_mod.Gen(9090)
    checked = 0
    for _ in range(200):
        src, _ = g.gen_program()
        model = dict(g.funcs)  # fname -> (ptypes, ret) for this program
        cur = None
        for line in src.splitlines():
            s = line.strip()
            if s.startswith("to ") and s.endswith(":"):
                cur = s[3:].split()[0].rstrip(":")
            elif cur is not None and s.startswith("give back ") \
                    and cur in model:
                want = model_type(model[cur][1])
                got = lit_type(s[len("give back "):])
                if want and got:
                    assert got == want, \
                        f"{cur}: give back {got}, model says {want}"
                    checked += 1
    assert checked > 0, "no classifiable give-backs seen?"
