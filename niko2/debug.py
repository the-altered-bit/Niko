"""Debugger core for Niko 2: breakpoints and stepping on top of the VM.

The VM (`niko2/vm.py`) calls `vm.trace_fn(vm, frames, frame, ins)` before
every instruction when `trace_fn` is set. This module's `Debugger` uses
that hook to pause on breakpoints and to implement step-in/over/out, then
blocks the VM thread until the client resumes it. The DAP adapter
(`niko2/dap.py`) drives it; the same class could back a CLI debugger.

Pause granularity is one source line: the hook only considers pausing
when execution arrives at a new (frame, line) pair, so a line that
compiles to several instructions pauses once, not once per instruction.

Conditional breakpoints (Alpha 21): a breakpoint may carry a condition
expression, which is evaluated at hit time against the current frame's
locals (the same machinery as `evaluate()`) -- the breakpoint stops only
when the result is truthy, the same `bool()` test the VM uses for `if`.
A condition that fails to parse, typecheck, or evaluate surfaces a
warning and stops anyway; the session never dies or hangs on a bad
condition.

Multi-file programs (Alpha 18) go through the module pipeline
(`niko2/modules.py`) before compiling: every reachable module is
typechecked and the graph is desugared to one Program whose per-module
wrapper functions (`__import$mK`, nested defs `__import$mK$name`) keep
their original line numbers. A frame's qualname therefore identifies its
source file by prefix, so breakpoints, stepping, and stack traces follow
execution across files at one-line granularity.

`ask` (Alpha 18) works under the debugger: the VM reads input through
`vm.input_fn`, which the Debugger routes to its `on_input` callback
(prompt -> str, called on the VM thread). When no callback is set it
falls back to console stdin.
"""
import io
import threading
from contextlib import redirect_stdout

from .typecheck import Checker, TypeErrorNiko
from .compiler import compile_ast
from .vm import VM, NikoRuntimeError, VMFunction, Cell, fmt


class _KillSignal(Exception):
    """Raised inside the VM loop to unwind it when the session ends."""


class _EvalBudgetExceeded(Exception):
    """Raised by the evaluate instruction counter (wrapped by the VM
    into a NikoRuntimeError, whose message evaluate() reports)."""


class Debugger:
    def __init__(self, path):
        self.path = str(path)
        # path -> {1-based line: condition source text or None}. A None (or
        # empty) condition is an unconditional breakpoint, evaluated never.
        self.breakpoints = {}
        self.vm = VM()
        self.vm.trace_fn = self._on_ins
        self.vm.input_fn = self._ask_input
        self.on_input = None           # prompt -> str, called on the VM thread
        self._module_files = {}        # '__import$mK' -> absolute module path
        self._entry_path = str(path)
        self._resume = threading.Event()
        self._resume.set()
        self._lock = threading.Lock()
        self._paused = None             # list of (name, env-dict, line) when paused
        self._stop_reason = None
        self._step = None               # None | 'in' | 'over' | 'out'
        self._step_depth = 0
        self._last_key = None
        self._entered = False
        self.stop_on_entry = False
        self._killed = False
        self.runtime_error = None
        self.on_pause = lambda reason: None      # called on the VM thread
        self.on_terminated = lambda: None        # called on the VM thread
        self.on_output = lambda text: None       # called on the VM thread

    # -- configuration -------------------------------------------------

    def set_breakpoints(self, path, conds):
        """Replace the breakpoints for a file.

        `conds` maps 1-based lines to condition source text (or None for
        an unconditional breakpoint). A plain iterable of lines is also
        accepted and means "no conditions".
        """
        if isinstance(conds, dict):
            self.breakpoints[str(path)] = dict(conds)
        else:
            self.breakpoints[str(path)] = {line: None for line in conds}

    def breakpoint_lines(self):
        return sorted(self.breakpoints.get(self.path, ()))

    # -- running ---------------------------------------------------------

    def run(self):
        """Compile and execute the program on the calling thread. Returns
        when the program finishes, raises, or the session is killed.

        Multi-file programs go through the module pipeline: every
        reachable module is typechecked, then the graph is desugared to
        one Program whose per-module wrapper functions keep their
        original line numbers, so the session can pause inside imported
        modules.
        """
        # Local import: modules.py pulls in packaging pieces the debugger
        # doesn't need at import time.
        from .modules import (build_module_graph, check_units,
                              desugar_imports, _wrapper_name,
                              _use_wrapper_name)
        buf = _OutputForwarder(self.on_output)
        try:
            graph = build_module_graph(self.path)
            check_units(graph)
            self._entry_path = str(graph[-1].path)
            self._module_files = {}
            for u in graph[:-1]:
                w = (_use_wrapper_name(u.key) if u.kind == 'use'
                     else _wrapper_name(u.key))
                self._module_files[w] = str(u.path)
            module = compile_ast(desugar_imports(graph))
            with redirect_stdout(buf):
                self.vm.run_module(module, {})
        except _KillSignal:
            pass
        except Exception as e:
            # Compile-time problems (parse/check/import errors) and
            # runtime errors both end the session with a message and a
            # terminated event, instead of leaving the client hanging.
            self.runtime_error = e
            self.on_output(f'Niko error: {e}\n')
        finally:
            buf.flush()
            self.on_terminated()

    # -- source mapping ----------------------------------------------------

    def _frame_path(self, frame):
        """Absolute source path for a VM frame.

        Module wrappers are `__import$mK` / `__use$uK` and their nested
        defs are `__import$mK$name` / `__use$uK$name`, so the frame's
        qualname identifies its module by prefix; anything else belongs
        to the entry file.
        """
        qual = getattr(frame, 'qualname', None) or frame.name
        for prefix, path in self._module_files.items():
            if qual == prefix or qual.startswith(prefix + '$'):
                return path
        return self._entry_path

    # -- the VM hook -------------------------------------------------------

    def _on_ins(self, vm, frames, frame, ins):
        if self._killed:
            raise _KillSignal()
        line = ins.line
        key = (id(frame), line)
        if key == self._last_key:
            return
        self._last_key = key
        depth = len(frames)
        reason = None
        if self.stop_on_entry and not self._entered:
            self._entered = True
            reason = 'entry'
        elif line in self.breakpoints.get(self._frame_path(frame), ()):
            cond = self.breakpoints[self._frame_path(frame)][line]
            if self._condition_holds(frames, ins, cond):
                reason = 'breakpoint'
        elif self._step == 'in':
            reason = 'step'
        elif self._step == 'over' and depth <= self._step_depth:
            reason = 'step'
        elif self._step == 'out' and depth < self._step_depth:
            reason = 'step'
        if reason is not None:
            self._pause(reason, frames, ins)

    def _condition_holds(self, frames, ins, cond):
        """True when a reached breakpoint should actually stop.

        No condition (or a blank one) means unconditional: stop, today's
        behavior. Otherwise the condition is evaluated against the current
        frames exactly like the `evaluate` request (typechecked against
        the locals, run on a fresh VM with an instruction budget), and we
        stop only when the result is truthy -- the same `bool()` test the
        VM uses for `if`.

        A condition that fails to parse, typecheck, or evaluate must not
        silently swallow the breakpoint or kill the session: the error is
        surfaced as a warning and we stop anyway, so the user sees both
        the stop and what went wrong.
        """
        if not cond or not str(cond).strip():
            return True
        # evaluate() reads the paused snapshot, so build one temporarily
        # (we are on the VM thread here; nothing else can be paused) and
        # clear it again unless we stop.
        with self._lock:
            self._paused = self._snapshot(frames, ins)
        try:
            ok, value = self.evaluate_value(str(cond), 0)
        except Exception as e:  # evaluate must never raise out of here
            ok, value = False, f'condition check crashed: {e}'
        finally:
            with self._lock:
                self._paused = None
        if not ok:
            self.on_output(f'Niko warning: breakpoint condition '
                           f'"{cond}" failed: {value}\n')
            return True
        try:
            return bool(value)
        except Exception as e:
            self.on_output(f'Niko warning: breakpoint condition '
                           f'"{cond}" failed: {e}\n')
            return True

    def _snapshot(self, frames, ins):
        """Outermost-first snapshot list, as stored in `self._paused`."""
        snap = []
        for fr in frames:
            ln = fr.code[fr.ip - 1].line if fr.ip > 0 else ins.line
            snap.append((fr.name, dict(fr.env), ln, self._frame_path(fr)))
        return snap

    def _pause(self, reason, frames, ins):
        snap = self._snapshot(frames, ins)
        with self._lock:
            self._paused = snap
            self._stop_reason = reason
        self._resume.clear()
        self.on_pause(reason)
        while not self._resume.wait(timeout=0.2):
            if self._killed:
                raise _KillSignal()
        if self._killed:
            raise _KillSignal()

    # -- client control (called from the adapter thread) -------------------

    def _go(self, step=None):
        with self._lock:
            depth = len(self._paused) if self._paused else 0
            self._paused = None
            self._stop_reason = None
            self._step = step
            self._step_depth = depth
        self._resume.set()

    def resume(self):
        self._go(None)

    def step_in(self):
        self._go('in')

    def step_over(self):
        self._go('over')

    def step_out(self):
        self._go('out')

    def kill(self):
        self._killed = True
        self._resume.set()

    # -- inspection (valid while paused) -------------------------------------

    @property
    def paused(self):
        with self._lock:
            return self._paused is not None

    def stack(self):
        """Innermost-first list of (name, env-dict, line, path)."""
        with self._lock:
            return list(reversed(self._paused or []))

    def stop_reason(self):
        with self._lock:
            return self._stop_reason

    # -- input and evaluation (called on the VM thread) ----------------------

    def _ask_input(self, prompt):
        """The VM's `ask` statement reads through here.

        The DAP adapter sets `on_input` to answer via a DAP reverse
        request; with no callback this falls back to console stdin (for a
        standalone CLI debugger, where stdin really is the console).
        """
        if self.on_input is not None:
            return self.on_input(prompt)
        return input(prompt)

    def evaluate(self, expr_text, frame_index=0):
        """Evaluate a simple expression against a paused frame's locals.

        Returns (True, rendered value) or (False, error message). The
        expression is typechecked with the frame's names defined, then run
        on a fresh VM (no trace hook, so it can't re-pause the session)
        with an instruction budget, so a runaway expression can't hang
        anything. The paused program's own state is untouched except for
        side effects the expression itself performs.
        """
        ok, value = self.evaluate_value(expr_text, frame_index)
        if not ok:
            return False, value
        return True, describe_value(value)

    def evaluate_value(self, expr_text, frame_index=0):
        """Like evaluate(), but returns the raw value instead of rendering
        it. Used by conditional breakpoints for the truthiness test."""
        from .parser import parse_expr, ParseError
        from .ast import Program, SetStmt
        stack = self.stack()
        if not (0 <= frame_index < len(stack)):
            return False, f'no such frame: {frame_index}'
        _name, env, line, _path = stack[frame_index]
        try:
            expr = parse_expr(expr_text, line)
        except ParseError as e:
            return False, str(e)
        checker = Checker()
        for k, v in env.items():
            checker.define(k, _type_of_value(v), line)
        prog = Program(line, [SetStmt(line, '__evalresult', expr)])
        try:
            checker.check(prog)
        except TypeErrorNiko as e:
            return False, str(e)
        module = compile_ast(prog)
        vm = VM()
        vm.input_fn = self.vm.input_fn
        budget = [500000]

        def _count(_vm, _frames, _frame, _ins):
            budget[0] -= 1
            if budget[0] <= 0:
                raise _EvalBudgetExceeded('evaluation exceeded its '
                                          'instruction budget')

        vm.trace_fn = _count
        eval_env = {k: (v.value if isinstance(v, Cell) else v)
                    for k, v in env.items()}
        try:
            vm.run_module(module, eval_env)
        except NikoRuntimeError as e:
            return False, str(e)
        return True, eval_env.get('__evalresult')


class _OutputForwarder(io.TextIOBase):
    """A stdout replacement that forwards complete lines to a callback."""

    def __init__(self, callback):
        self._callback = callback
        self._buf = ''

    def write(self, s):
        self._buf += s
        while '\n' in self._buf:
            line, self._buf = self._buf.split('\n', 1)
            self._callback(line + '\n')
        return len(s)

    def flush(self):
        if self._buf:
            self._callback(self._buf)
            self._buf = ''


def _type_of_value(v):
    """A Niko type for a runtime value, for the evaluate() checker."""
    from .typecheck import ANY, NUMBER, TEXT, BOOLEAN, LIST, MAP, FUNCTION, NOTHING
    if isinstance(v, Cell):
        v = v.value
    if v is None:
        return NOTHING
    if isinstance(v, bool):
        return BOOLEAN
    if isinstance(v, (int, float)):
        return NUMBER
    if isinstance(v, str):
        return TEXT
    if isinstance(v, list):
        return LIST
    if isinstance(v, dict):
        return MAP
    if isinstance(v, VMFunction):
        return FUNCTION
    return ANY


def describe_value(v):
    # Alpha 10: captured variables live in shared Cells; show the value.
    if isinstance(v, Cell):
        v = v.value
    if isinstance(v, VMFunction):
        params = ', '.join(v.code.params)
        return f'function {v.code.name}({params})'
    return fmt(v)


def type_of_value(v):
    if isinstance(v, Cell):
        v = v.value
    if v is None:
        return 'nothing'
    if isinstance(v, bool):
        return 'yes/no'
    if isinstance(v, (int, float)):
        return 'number'
    if isinstance(v, str):
        return 'text'
    if isinstance(v, list):
        return 'list'
    if isinstance(v, dict):
        return 'record'
    if isinstance(v, VMFunction):
        return 'function'
    return type(v).__name__
