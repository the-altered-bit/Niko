"""Niko 2 debug adapter (Debug Adapter Protocol over stdio).

Start it with `niko2 debug`. It speaks enough DAP for real debugging in
VS Code (via the bundled Niko extension) or any DAP-capable client:

- `launch` with a `program` path, optional `stopOnEntry`
- `setBreakpoints` (line breakpoints, verified; works in the entry file
  and in any imported module; DAP `condition` fields are honored -- the
  condition is evaluated against the paused frame's locals at hit time
  and the breakpoint stops only when it is truthy)
- `continue`, `next` (step over), `stepIn`, `stepOut`
- `threads`, `stackTrace` (each frame carries its own file, so the editor
  opens imported modules), `scopes`, `variables` (locals, with one level
  of list/record expansion)
- `evaluate` (simple expressions against a paused frame's locals)
- `output` events carry the program's `say` output; `terminated` ends
  the session

The program runs on a worker thread; the adapter thread owns the
protocol. All writes to stdout go through one lock so events from the
worker thread can't interleave with responses.

`ask` (Alpha 18): the debuggee's stdin is the DAP protocol stream, so the
adapter answers `ask` with a DAP *reverse* request (`input`, carrying the
prompt) to the client and waits for its response. A client that answers
keeps the program going; a client that errors or stays silent gets a
bounded wait (30s), after which the program receives "" and a warning is
emitted -- the session can never hang on input. Note this is a
Niko-specific reverse request: stock VS Code does not answer it, so under
VS Code `ask` currently yields "" after the timeout.

Limits (Alpha 18): `use` imports are still not loaded under the debugger
(a program with `use` lines fails with a clear "I don't know what ..."
runtime error naming the missing name).
"""
import os
import sys
import threading
import time

from .jsonrpc import read_message, write_message
from .debug import Debugger, describe_value, type_of_value


class Adapter:
    def __init__(self, stdin, stdout):
        self.stdin = stdin
        self.stdout = stdout
        self._write_lock = threading.Lock()
        self._seq = 0
        self.debugger = None
        self._worker = None
        self._var_refs = {}
        self._next_ref = 3000
        self._exited = False
        # Alpha 18: pending `ask` reverse requests: seq -> [Event, box].
        # Guarded by _input_lock; the VM worker thread waits on the Event
        # while the adapter thread keeps serving the protocol.
        self._input_lock = threading.Lock()
        self._input_waiters = {}
        self._input_timeout = 30

    # -- protocol plumbing -------------------------------------------------

    def _send_raw(self, obj):
        """Send a message already carrying type/command; returns its seq."""
        with self._write_lock:
            self._seq += 1
            obj = dict(obj)
            obj['seq'] = self._seq
            write_message(self.stdout, obj)
            return self._seq

    def _send(self, obj):
        return self._send_raw(obj)

    def _respond(self, req, body=None, success=True):
        msg = {'type': 'response', 'request_seq': req['seq'],
               'success': success, 'command': req['command']}
        if body is not None:
            msg['body'] = body
        self._send(msg)

    def _event(self, event, body=None):
        msg = {'type': 'event', 'event': event}
        if body is not None:
            msg['body'] = body
        self._send(msg)

    def _fail(self, req, message):
        self._send({'type': 'response', 'request_seq': req['seq'],
                    'success': False, 'command': req['command'],
                    'message': message})

    # -- debugger lifecycle --------------------------------------------------

    def _run_debugger(self):
        try:
            self.debugger.run()
        except Exception as e:  # never let the worker die silently
            self._event('output', {'category': 'stderr',
                                   'output': f'debugger error: {e}\n'})

    def _ensure_debugger(self, req, program):
        dbg = Debugger(os.path.abspath(program))
        dbg.on_pause = lambda reason: self._event(
            'stopped', {'reason': reason, 'threadId': 1,
                        'description': f'Niko: {reason}'})
        dbg.on_terminated = lambda: self._event('terminated')
        dbg.on_output = lambda text: self._event(
            'output', {'category': 'stdout', 'output': text})
        dbg.on_input = self._debuggee_input
        self.debugger = dbg
        return dbg

    # -- `ask` under the debugger --------------------------------------------

    def _debuggee_input(self, prompt):
        """Answer the debuggee's `ask` via a DAP reverse `input` request.

        Called on the VM worker thread. The adapter thread keeps serving
        the protocol, so the client's response is picked up by
        `_on_reverse_response` below. The wait is bounded (and wakes early
        on disconnect), so a client that never answers can't hang the
        session: the program gets "" and a warning goes to the console.
        """
        self._event('output', {'category': 'stdout', 'output': prompt})
        ev = threading.Event()
        box = {}
        with self._input_lock:
            seq = self._send_raw({'type': 'request', 'command': 'input',
                                  'arguments': {'prompt': prompt}})
            self._input_waiters[seq] = (ev, box)
        deadline = time.monotonic() + self._input_timeout
        while not ev.is_set():
            if self.debugger is not None and self.debugger._killed:
                break  # disconnect: wake promptly, don't wait out input
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break  # absolute bound even if the client stays silent
            ev.wait(timeout=min(0.2, remaining))
        with self._input_lock:
            self._input_waiters.pop(seq, None)
        if 'text' in box:
            return box['text']
        self._event('output', {'category': 'stderr',
                               'output': 'ask: no answer from the debug '
                                         'client; using ""\n'})
        return ''

    def _on_reverse_response(self, msg):
        """Handle a client's response to one of our reverse requests."""
        seq = msg.get('request_seq')
        with self._input_lock:
            waiter = self._input_waiters.get(seq)
        if waiter is None or msg.get('command') != 'input':
            return
        ev, box = waiter
        if msg.get('success'):
            body = msg.get('body') or {}
            if 'text' in body:
                box['text'] = body['text']
        ev.set()

    # -- dispatch --------------------------------------------------------------

    def handle(self, msg):
        if msg.get('type') == 'response':
            # A client's answer to one of our reverse requests (e.g. the
            # `input` request the `ask` implementation sends).
            self._on_reverse_response(msg)
            return True
        if msg.get('type') != 'request':
            return True
        cmd = msg.get('command')
        args = msg.get('arguments', {})
        try:
            handler = getattr(self, '_on_' + cmd, None)
            if handler is None:
                self._fail(msg, f'unsupported command: {cmd}')
            else:
                handler(msg, args)
        except Exception as e:
            self._fail(msg, str(e))
        return not self._exited

    def serve(self):
        while True:
            msg = read_message(self.stdin)
            if msg is None:
                break
            if not self.handle(msg):
                break

    # -- requests ------------------------------------------------------------------

    def _on_initialize(self, req, args):
        self._respond(req, {'supportsConfigurationDoneRequest': True,
                            'supportsEvaluateForHovers': True})
        self._event('initialized')

    def _on_launch(self, req, args):
        program = args.get('program')
        if not program:
            self._fail(req, 'launch needs a "program" path')
            return
        dbg = self._ensure_debugger(req, program)
        dbg.stop_on_entry = bool(args.get('stopOnEntry', False))
        # Test hook (also useful for clients that never answer `input`):
        # bound how long `ask` waits for the client's answer.
        if 'inputTimeout' in args:
            self._input_timeout = float(args['inputTimeout'])
        self._respond(req)

    def _on_setBreakpoints(self, req, args):
        source = args.get('source', {})
        path = os.path.abspath(source.get('path', ''))
        # Alpha 21: DAP SourceBreakpoint `condition` fields. A missing or
        # blank condition is unconditional (the behavior so far); a
        # non-blank one is evaluated in the paused frame's context at hit
        # time and stops only when truthy.
        conds = {}
        for b in args.get('breakpoints', []):
            if 'line' not in b:
                continue
            cond = b.get('condition')
            if cond is not None and not str(cond).strip():
                cond = None
            conds[b['line']] = cond
        if self.debugger is None:
            self._fail(req, 'no active launch')
            return
        # Breakpoints may target the launch program or any module it
        # imports: the debugger maps each paused frame back to its source
        # file (Alpha 18), so cross-file breakpoints hit.
        self.debugger.set_breakpoints(path, conds)
        self._respond(req, {'breakpoints': [
            {'verified': True, 'line': ln} for ln in sorted(conds)]})

    def _on_configurationDone(self, req, args):
        if self.debugger is None:
            self._fail(req, 'no active launch')
            return
        self._worker = threading.Thread(target=self._run_debugger, daemon=True)
        self._worker.start()
        self._respond(req)

    def _on_threads(self, req, args):
        self._respond(req, {'threads': [{'id': 1, 'name': 'main'}]})

    def _on_stackTrace(self, req, args):
        frames = []
        if self.debugger is not None and self.debugger.paused:
            for i, (name, env, line, path) in enumerate(self.debugger.stack()):
                frames.append({
                    'id': i,
                    'name': name,
                    'line': line,
                    'column': 1,
                    'source': {'name': os.path.basename(path),
                               'path': path},
                })
        self._respond(req, {'stackFrames': frames, 'totalFrames': len(frames)})

    def _on_scopes(self, req, args):
        frame_id = args.get('frameId', 0)
        self._respond(req, {'scopes': [
            {'name': 'Locals', 'variablesReference': 2000 + frame_id,
             'expensive': False}]})

    def _alloc_ref(self, value):
        self._next_ref += 1
        self._var_refs[self._next_ref] = value
        return self._next_ref

    def _var(self, name, value):
        ref = 0
        if isinstance(value, (list, dict)) and value:
            ref = self._alloc_ref(value)
        return {'name': str(name), 'value': describe_value(value),
                'type': type_of_value(value), 'variablesReference': ref}

    def _on_variables(self, req, args):
        ref = args.get('variablesReference', 0)
        variables = []
        if self.debugger is not None and self.debugger.paused:
            if 2000 <= ref < 3000:
                stack = self.debugger.stack()
                idx = ref - 2000
                if 0 <= idx < len(stack):
                    _name, env, _line, _path = stack[idx]
                    for k in sorted(env):
                        if k.startswith('$'):
                            continue  # hidden compiler slots
                        variables.append(self._var(k, env[k]))
            elif ref in self._var_refs:
                value = self._var_refs[ref]
                if isinstance(value, list):
                    for i, v in enumerate(value):
                        variables.append(self._var(f'[{i + 1}]', v))
                elif isinstance(value, dict):
                    for k in sorted(value, key=str):
                        variables.append(self._var(k, value[k]))
        self._respond(req, {'variables': variables})

    def _on_continue(self, req, args):
        if self.debugger is None:
            self._fail(req, 'no active launch')
            return
        self._respond(req, {'allThreadsContinued': True})
        self.debugger.resume()
        self._event('continued', {'threadId': 1, 'allThreadsContinued': True})

    def _on_next(self, req, args):
        if self.debugger is None or not self.debugger.paused:
            self._fail(req, 'not paused')
            return
        self._respond(req)
        self.debugger.step_over()

    def _on_stepIn(self, req, args):
        if self.debugger is None or not self.debugger.paused:
            self._fail(req, 'not paused')
            return
        self._respond(req)
        self.debugger.step_in()

    def _on_stepOut(self, req, args):
        if self.debugger is None or not self.debugger.paused:
            self._fail(req, 'not paused')
            return
        self._respond(req)
        self.debugger.step_out()

    def _on_evaluate(self, req, args):
        # Alpha 18: simple expressions against a paused frame's locals.
        if self.debugger is None or not self.debugger.paused:
            self._fail(req, 'not paused')
            return
        expr = args.get('expression', '')
        frame_id = args.get('frameId', 0)
        try:
            ok, text = self.debugger.evaluate(expr, frame_id)
        except Exception as e:
            self._fail(req, f'evaluate failed: {e}')
            return
        if ok:
            self._respond(req, {'result': text, 'variablesReference': 0})
        else:
            self._fail(req, text)

    def _on_disconnect(self, req, args):
        if self.debugger is not None:
            self.debugger.kill()
        if self._worker is not None:
            self._worker.join(timeout=5)
        self._respond(req)
        self._exited = True


def main():
    adapter = Adapter(sys.stdin.buffer, sys.stdout.buffer)
    adapter.serve()
    return 0
