"""Debugger core for Niko 2: breakpoints and stepping on top of the VM.

The VM (`niko2/vm.py`) calls `vm.trace_fn(vm, frames, frame, ins)` before
every instruction when `trace_fn` is set. This module's `Debugger` uses
that hook to pause on breakpoints and to implement step-in/over/out, then
blocks the VM thread until the client resumes it. The DAP adapter
(`niko2/dap.py`) drives it; the same class could back a CLI debugger.

Pause granularity is one source line: the hook only considers pausing
when execution arrives at a new (frame, line) pair, so a line that
compiles to several instructions pauses once, not once per instruction.
"""
import io
import threading
from contextlib import redirect_stdout
from pathlib import Path

from .parser import parse
from .typecheck import check
from .compiler import compile_ast
from .vm import VM, NikoRuntimeError, VMFunction, Cell, fmt


class _KillSignal(Exception):
    """Raised inside the VM loop to unwind it when the session ends."""


class Debugger:
    def __init__(self, path):
        self.path = str(path)
        self.breakpoints = {}          # path -> set of 1-based lines
        self.vm = VM()
        self.vm.trace_fn = self._on_ins
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

    def set_breakpoints(self, path, lines):
        self.breakpoints[str(path)] = set(lines)

    def breakpoint_lines(self):
        return sorted(self.breakpoints.get(self.path, ()))

    # -- running ---------------------------------------------------------

    def run(self):
        """Compile and execute the program on the calling thread. Returns
        when the program finishes, raises, or the session is killed."""
        src = Path(self.path).read_text(encoding='utf8')
        tree = parse(src)
        check(tree, [])
        from .ast import Program
        module = compile_ast(Program(tree.line, tree.body))
        buf = _OutputForwarder(self.on_output)
        try:
            with redirect_stdout(buf):
                self.vm.run_module(module, {})
        except _KillSignal:
            pass
        except NikoRuntimeError as e:
            self.runtime_error = e
            self.on_output(f'Niko error: {e}\n')
        finally:
            buf.flush()
            self.on_terminated()

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
        elif line in self.breakpoints.get(self.path, ()):
            reason = 'breakpoint'
        elif self._step == 'in':
            reason = 'step'
        elif self._step == 'over' and depth <= self._step_depth:
            reason = 'step'
        elif self._step == 'out' and depth < self._step_depth:
            reason = 'step'
        if reason is not None:
            self._pause(reason, frames, ins)

    def _pause(self, reason, frames, ins):
        snap = []
        for fr in frames:
            ln = fr.code[fr.ip - 1].line if fr.ip > 0 else ins.line
            snap.append((fr.name, dict(fr.env), ln))
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
        """Innermost-first list of (name, env-dict, line)."""
        with self._lock:
            return list(reversed(self._paused or []))

    def stop_reason(self):
        with self._lock:
            return self._stop_reason


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
