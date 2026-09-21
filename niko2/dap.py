"""Niko 2 debug adapter (Debug Adapter Protocol over stdio).

Start it with `niko2 debug`. It speaks enough DAP for real debugging in
VS Code (via the bundled Niko extension) or any DAP-capable client:

- `launch` with a `program` path, optional `stopOnEntry`
- `setBreakpoints` (line breakpoints, verified)
- `continue`, `next` (step over), `stepIn`, `stepOut`
- `threads`, `stackTrace`, `scopes`, `variables` (locals, with one level
  of list/record expansion)
- `output` events carry the program's `say` output; `terminated` ends
  the session

The program runs on a worker thread; the adapter thread owns the
protocol. All writes to stdout go through one lock so events from the
worker thread can't interleave with responses.

Limits (Alpha 7): single-file programs (`use` imports aren't loaded);
`ask` for input isn't supported while debugging — the program would be
reading the debug protocol stream.
"""
import os
import sys
import threading

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

    # -- protocol plumbing -------------------------------------------------

    def _send(self, obj):
        with self._write_lock:
            self._seq += 1
            obj = dict(obj)
            obj['seq'] = self._seq
            write_message(self.stdout, obj)

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
        self.debugger = dbg
        return dbg

    # -- dispatch --------------------------------------------------------------

    def handle(self, msg):
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
        self._respond(req, {'supportsConfigurationDoneRequest': True})
        self._event('initialized')

    def _on_launch(self, req, args):
        program = args.get('program')
        if not program:
            self._fail(req, 'launch needs a "program" path')
            return
        dbg = self._ensure_debugger(req, program)
        dbg.stop_on_entry = bool(args.get('stopOnEntry', False))
        self._respond(req)

    def _on_setBreakpoints(self, req, args):
        source = args.get('source', {})
        path = os.path.abspath(source.get('path', ''))
        lines = [b['line'] for b in args.get('breakpoints', []) if 'line' in b]
        if self.debugger is None:
            self._fail(req, 'no active launch')
            return
        # The debugger was created for the launch program; breakpoints for
        # any other path are recorded but never hit (single-file programs
        # in Alpha 7).
        self.debugger.set_breakpoints(path, lines)
        self._respond(req, {'breakpoints': [
            {'verified': True, 'line': ln} for ln in lines]})

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
            for i, (name, env, line) in enumerate(self.debugger.stack()):
                frames.append({
                    'id': i,
                    'name': name,
                    'line': line,
                    'column': 1,
                    'source': {'name': os.path.basename(self.debugger.path),
                               'path': self.debugger.path},
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
                    _name, env, _line = stack[idx]
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
