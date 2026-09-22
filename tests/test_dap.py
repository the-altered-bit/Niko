"""Alpha 7: the DAP debug adapter, tested end to end over stdio.

Spawns `python -m niko2 debug` and speaks real DAP against a small
program: launch, set a breakpoint, hit it three times, inspect the
stack and the `total` variable, step once, continue to termination.

Alpha 18 extends this file: multi-file debugging (breakpoints, stepping,
and stack frames inside imported modules), `ask` under the debugger
(answered through the adapter's reverse `input` request, with a timeout
fallback so the session can never hang on input), and the `evaluate`
request.
"""
import json
import os
import pathlib
import select
import signal
import subprocess
import sys
import tempfile

# Never hang the suite: a regression that wedges the adapter fails loudly
# (SIGALRM terminates the run) instead of blocking forever on a read.
signal.alarm(900)

root = pathlib.Path(__file__).resolve().parent.parent

PROGRAM = '''set total to 0
repeat 3 times:
    set total to total + 1
    say "count", total
say "done"
'''


class Client:
    def __init__(self):
        env = dict(os.environ, PYTHONPATH=str(root))
        self.proc = subprocess.Popen(
            [sys.executable, '-m', 'niko2', 'debug'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            cwd=root, env=env,
        )
        self._seq = 0
        self.stash = []

    def _send(self, obj):
        body = json.dumps(obj).encode('utf-8')
        self.proc.stdin.write(b'Content-Length: ' + str(len(body)).encode() + b'\r\n\r\n' + body)
        self.proc.stdin.flush()

    def _read(self):
        headers = {}
        while True:
            line = self.proc.stdout.readline()
            assert line, 'adapter closed stdout'
            line = line.strip()
            if not line:
                break
            k, v = line.split(b':', 1)
            headers[k.strip().lower()] = v.strip()
        body = self.proc.stdout.read(int(headers[b'content-length']))
        return json.loads(body)

    def request(self, command, arguments=None):
        self._seq += 1
        self._send({'seq': self._seq, 'type': 'request',
                    'command': command, 'arguments': arguments or {}})
        while True:
            msg = self._read()
            if msg.get('type') == 'response' and msg.get('request_seq') == self._seq:
                assert msg.get('success'), f'{command} failed: {msg}'
                return msg.get('body', {})
            self.stash.append(msg)

    def wait_event(self, name, timeout_reads=400):
        for msg in list(self.stash):
            if msg.get('type') == 'event' and msg.get('event') == name:
                self.stash.remove(msg)
                return msg.get('body', {})
        for _ in range(timeout_reads):
            msg = self._read()
            if msg.get('type') == 'event' and msg.get('event') == name:
                return msg.get('body', {})
            self.stash.append(msg)
        raise AssertionError(f'timed out waiting for event {name}; stash={self.stash!r}')

    def close(self):
        try:
            self.proc.stdin.close()
        except BrokenPipeError:
            pass
        self.proc.wait(timeout=15)


with tempfile.NamedTemporaryFile('w', suffix='.niko', delete=False) as f:
    f.write(PROGRAM)
    prog = f.name

client = Client()
try:
    caps = client.request('initialize', {'adapterID': 'niko-test'})
    assert caps.get('supportsConfigurationDoneRequest') is True
    init_ev = client.wait_event('initialized')

    client.request('launch', {'program': prog})
    bp = client.request('setBreakpoints', {
        'source': {'name': 't.niko', 'path': prog},
        'breakpoints': [{'line': 4}]})
    assert bp['breakpoints'] == [{'verified': True, 'line': 4}], bp
    client.request('configurationDone')

    outputs = []
    for hit in (1, 2, 3):
        stopped = client.wait_event('stopped')
        assert stopped['reason'] == 'breakpoint', stopped
        assert stopped['threadId'] == 1

        if hit == 1:
            # inspect the paused state on the first hit
            threads = client.request('threads')
            assert threads['threads'] == [{'id': 1, 'name': 'main'}]
            trace = client.request('stackTrace', {'threadId': 1})
            assert trace['totalFrames'] >= 1
            top = trace['stackFrames'][0]
            assert top['line'] == 4, top
            scopes = client.request('scopes', {'frameId': top['id']})
            assert scopes['scopes'][0]['name'] == 'Locals'
            var_ref = scopes['scopes'][0]['variablesReference']
            variables = client.request('variables', {'variablesReference': var_ref})
            by_name = {v['name']: v for v in variables['variables']}
            assert 'total' in by_name, by_name
            assert by_name['total']['value'] == '1', by_name['total']
            # step over the `say` line -> loops back to the repeat header (line 2)
            client.request('next')
            stepped = client.wait_event('stopped')
            assert stepped['reason'] == 'step', stepped
            trace2 = client.request('stackTrace', {'threadId': 1})
            assert trace2['stackFrames'][0]['line'] in (2, 3), trace2['stackFrames'][0]

        client.request('continue')
        # drain the `continued` event so it doesn't confuse wait_event
        client.wait_event('continued')

    # after the third hit's continue, the program runs to the end
    term = client.wait_event('terminated')
    # the `say` output should have arrived as output events along the way
    saw_count = any(
        m.get('type') == 'event' and m.get('event') == 'output'
        and 'count 3' in m.get('body', {}).get('output', '')
        for m in client.stash)
    assert saw_count, 'expected an output event with "count 3"'

    client.request('disconnect')
    print('test_dap.py: all assertions passed')
finally:
    client.close()
    os.unlink(prog)


# ---------------------------------------------------------------------------
# Alpha 18: multi-file debugging, `ask` under the debugger, `evaluate`.
#
# A timeout-guarded DAP client: every read is bounded via select, so a
# regression that hangs the adapter fails loudly instead of hanging the
# suite. It also understands the adapter's reverse `input` request, which
# is how `ask` is answered while debugging.


class TimedClient:
    def __init__(self, timeout=20):
        env = dict(os.environ, PYTHONPATH=str(root))
        self.proc = subprocess.Popen(
            [sys.executable, '-m', 'niko2', 'debug'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            cwd=root, env=env,
        )
        self._seq = 0
        self.stash = []
        self.timeout = timeout
        # Raw byte buffer for adapter output. NOTE: we read via os.read on
        # the raw fd, never via the BufferedReader: select() only sees
        # kernel-buffered bytes, so mixing select with readline() would
        # miss messages already pulled into the reader's userspace buffer.
        self._out_buf = b''
        self._out_fd = self.proc.stdout.fileno()

    def _send(self, obj):
        body = json.dumps(obj).encode('utf-8')
        self.proc.stdin.write(b'Content-Length: ' + str(len(body)).encode() + b'\r\n\r\n' + body)
        self.proc.stdin.flush()

    @staticmethod
    def _try_parse(buf):
        head, sep, rest = buf.partition(b'\r\n\r\n')
        if not sep:
            return None, buf
        headers = {}
        for line in head.split(b'\r\n'):
            if b':' in line:
                k, v = line.split(b':', 1)
                headers[k.strip().lower()] = v.strip()
        try:
            length = int(headers.get(b'content-length', 0))
        except ValueError:
            raise AssertionError(f'bad DAP framing: {buf[:200]!r}')
        if not length or len(rest) < length:
            return None, buf
        return json.loads(rest[:length].decode('utf-8')), rest[length:]

    def _read(self):
        while True:
            msg, self._out_buf = self._try_parse(self._out_buf)
            if msg is not None:
                return msg
            r, _, _ = select.select([self._out_fd], [], [], self.timeout)
            assert r, (f'timed out after {self.timeout}s waiting for adapter '
                       f'output; stash={self.stash!r}')
            chunk = os.read(self._out_fd, 65536)
            assert chunk, 'adapter closed stdout'
            self._out_buf += chunk

    def _request_raw(self, command, arguments=None):
        self._seq += 1
        self._send({'seq': self._seq, 'type': 'request',
                    'command': command, 'arguments': arguments or {}})
        while True:
            msg = self._read()
            if msg.get('type') == 'response' and msg.get('request_seq') == self._seq:
                return msg
            self.stash.append(msg)

    def request(self, command, arguments=None):
        msg = self._request_raw(command, arguments)
        assert msg.get('success'), f'{command} failed: {msg}'
        return msg.get('body', {})

    def wait_event(self, name):
        for msg in list(self.stash):
            if msg.get('type') == 'event' and msg.get('event') == name:
                self.stash.remove(msg)
                return msg.get('body', {})
        while True:
            msg = self._read()
            if msg.get('type') == 'event' and msg.get('event') == name:
                return msg.get('body', {})
            self.stash.append(msg)

    def wait_request(self, command):
        """Wait for a reverse request from the adapter (e.g. `input`)."""
        for msg in list(self.stash):
            if msg.get('type') == 'request' and msg.get('command') == command:
                self.stash.remove(msg)
                return msg
        while True:
            msg = self._read()
            if msg.get('type') == 'request' and msg.get('command') == command:
                return msg
            self.stash.append(msg)

    def respond(self, req, body):
        self._send({'type': 'response', 'request_seq': req['seq'],
                    'success': True, 'command': req['command'], 'body': body})

    def outputs(self):
        return [m.get('body', {}).get('output', '')
                for m in self.stash
                if m.get('type') == 'event' and m.get('event') == 'output']

    def close(self):
        try:
            self.proc.stdin.close()
        except BrokenPipeError:
            pass
        self.proc.wait(timeout=15)


def _alpha18_workdir():
    import shutil
    workdir = pathlib.Path(tempfile.mkdtemp(prefix='niko-dbg18-'))
    return workdir, shutil


def test_multifile_debugging():
    """Breakpoints, stepping, stack frames, and evaluate across an import."""
    workdir, shutil = _alpha18_workdir()
    try:
        mod = workdir / 'mymod.niko'
        mod.write_text('to add with a, b:\n    give back a + b\n')
        main = workdir / 'main.niko'
        main.write_text('import "mymod.niko" as m\n'
                        'set r to m.add(2, 3)\n'
                        'say r\n')
        mod_p, main_p = str(mod.resolve()), str(main.resolve())

        client = TimedClient()
        try:
            client.request('initialize', {'adapterID': 'niko-test'})
            client.wait_event('initialized')
            client.request('launch', {'program': main_p})
            bp = client.request('setBreakpoints', {
                'source': {'name': 'mymod.niko', 'path': mod_p},
                'breakpoints': [{'line': 2}]})
            assert bp['breakpoints'] == [{'verified': True, 'line': 2}], bp
            bp2 = client.request('setBreakpoints', {
                'source': {'name': 'main.niko', 'path': main_p},
                'breakpoints': [{'line': 3}]})
            assert bp2['breakpoints'] == [{'verified': True, 'line': 3}], bp2
            client.request('configurationDone')

            # the breakpoint inside the imported module hits, and the stack
            # frame names the module file + line
            stopped = client.wait_event('stopped')
            assert stopped['reason'] == 'breakpoint', stopped
            trace = client.request('stackTrace', {'threadId': 1})
            top = trace['stackFrames'][0]
            assert top['name'] == 'add', top
            assert top['line'] == 2, top
            assert top['source']['path'] == mod_p, top
            assert trace['stackFrames'][1]['source']['path'] == main_p, trace

            # evaluate against the paused module frame ...
            ev = client.request('evaluate', {'expression': 'a + b',
                                             'frameId': 0, 'context': 'repl'})
            assert ev['result'] == '5', ev
            # ... an unknown name is a clean failure, not a wedged session
            bad = client._request_raw('evaluate', {'expression': 'nosuchname',
                                                   'frameId': 0})
            assert bad['success'] is False, bad
            # ... and another paused frame works too
            ev2 = client.request('evaluate', {'expression': '2 + 3',
                                              'frameId': 1, 'context': 'repl'})
            assert ev2['result'] == '5', ev2

            # step out of the module function: lands back in the entry file
            client.request('stepOut')
            stepped = client.wait_event('stopped')
            assert stepped['reason'] == 'step', stepped
            trace2 = client.request('stackTrace', {'threadId': 1})
            assert trace2['stackFrames'][0]['source']['path'] == main_p, trace2
            assert trace2['stackFrames'][0]['line'] == 2, trace2['stackFrames'][0]

            # continue: the entry-file breakpoint still works, and locals
            # are inspectable there
            client.request('continue')
            client.wait_event('continued')
            stopped2 = client.wait_event('stopped')
            assert stopped2['reason'] == 'breakpoint', stopped2
            trace3 = client.request('stackTrace', {'threadId': 1})
            assert trace3['stackFrames'][0]['line'] == 3, trace3
            assert trace3['stackFrames'][0]['source']['path'] == main_p
            scopes = client.request('scopes', {'frameId': 0})
            variables = client.request(
                'variables',
                {'variablesReference': scopes['scopes'][0]['variablesReference']})
            by_name = {v['name']: v for v in variables['variables']}
            assert by_name['r']['value'] == '5', by_name['r']

            client.request('continue')
            client.wait_event('continued')
            client.wait_event('terminated')
            assert any(o.strip() == '5' for o in client.outputs()), client.outputs()

            client.request('disconnect')
            print('test_dap.py (Alpha 18): multi-file debugging + evaluate passed')
        finally:
            client.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def test_ask_under_debugger():
    """`ask` is answered through the adapter's reverse `input` request."""
    workdir, shutil = _alpha18_workdir()
    try:
        prog = workdir / 'askme.niko'
        prog.write_text('ask "What is your name? " into name\n'
                        'say "hello " + name\n')
        client = TimedClient()
        try:
            client.request('initialize', {'adapterID': 'niko-test'})
            client.wait_event('initialized')
            client.request('launch', {'program': str(prog.resolve())})
            client.request('configurationDone')

            req = client.wait_request('input')
            assert req['arguments']['prompt'] == 'What is your name? ', req
            client.respond(req, {'text': 'Casper'})

            client.wait_event('terminated')
            assert any('hello Casper' in o for o in client.outputs()), client.outputs()

            client.request('disconnect')
            print('test_dap.py (Alpha 18): ask under debugger passed')
        finally:
            client.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def test_ask_timeout_fallback():
    """A client that never answers `input` can't hang the session: after
    the (short, launch-configured) timeout the program gets "" and a
    warning, then runs to termination."""
    workdir, shutil = _alpha18_workdir()
    try:
        prog = workdir / 'askme.niko'
        prog.write_text('ask "Name? " into name\nsay "hi " + name\n')
        client = TimedClient(timeout=25)
        try:
            client.request('initialize', {'adapterID': 'niko-test'})
            client.wait_event('initialized')
            client.request('launch', {'program': str(prog.resolve()),
                                      'inputTimeout': 1})
            client.request('configurationDone')

            # the adapter asks ... and we deliberately never answer
            client.wait_request('input')
            client.wait_event('terminated')
            outs = client.outputs()
            assert any('ask: no answer' in o for o in outs), outs
            assert any('hi ' in o for o in outs), outs

            client.request('disconnect')
            print('test_dap.py (Alpha 18): ask timeout fallback passed')
        finally:
            client.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


test_multifile_debugging()
test_ask_under_debugger()
test_ask_timeout_fallback()


# ---------------------------------------------------------------------------
# Alpha 21: conditional breakpoints.
#
# A DAP `condition` on a breakpoint is evaluated in the paused frame's
# context at hit time; the breakpoint stops only when it is truthy. A
# missing or blank condition is unconditional (the old behavior). A
# condition that fails to parse/typecheck/evaluate surfaces a warning
# and stops anyway -- the session never dies or hangs on a bad condition.

COND_PROGRAM = '''set i to 0
repeat 5 times:
    set i to i + 1
    say "i", i
say "done"
'''


def test_conditional_breakpoint():
    """The breakpoint fires exactly when the condition is true."""
    workdir, shutil = _alpha18_workdir()
    try:
        prog = workdir / 'cond.niko'
        prog.write_text(COND_PROGRAM)
        prog_p = str(prog.resolve())
        client = TimedClient()
        try:
            client.request('initialize', {'adapterID': 'niko-test'})
            client.wait_event('initialized')
            client.request('launch', {'program': prog_p})
            bp = client.request('setBreakpoints', {
                'source': {'name': 'cond.niko', 'path': prog_p},
                'breakpoints': [{'line': 4, 'condition': 'i == 3'}]})
            assert bp['breakpoints'] == [{'verified': True, 'line': 4}], bp
            client.request('configurationDone')

            # exactly one stop: the iteration where i == 3
            stopped = client.wait_event('stopped')
            assert stopped['reason'] == 'breakpoint', stopped
            trace = client.request('stackTrace', {'threadId': 1})
            assert trace['stackFrames'][0]['line'] == 4, trace
            scopes = client.request('scopes', {'frameId': 0})
            variables = client.request(
                'variables',
                {'variablesReference': scopes['scopes'][0]['variablesReference']})
            by_name = {v['name']: v for v in variables['variables']}
            assert by_name['i']['value'] == '3', by_name['i']

            client.request('continue')
            client.wait_event('continued')
            client.wait_event('terminated')
            # no second stop: every other iteration's condition was false
            assert not [m for m in client.stash
                        if m.get('type') == 'event'
                        and m.get('event') == 'stopped'], client.stash
            outs = client.outputs()
            for n in range(1, 6):
                assert any(f'i {n}' in o for o in outs), outs

            client.request('disconnect')
            print('test_dap.py (Alpha 21): conditional breakpoint passed')
        finally:
            client.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def test_conditional_breakpoint_never_true():
    """A condition that is never true stops never; the program just runs."""
    workdir, shutil = _alpha18_workdir()
    try:
        prog = workdir / 'cond.niko'
        prog.write_text(COND_PROGRAM)
        prog_p = str(prog.resolve())
        client = TimedClient()
        try:
            client.request('initialize', {'adapterID': 'niko-test'})
            client.wait_event('initialized')
            client.request('launch', {'program': prog_p})
            client.request('setBreakpoints', {
                'source': {'name': 'cond.niko', 'path': prog_p},
                'breakpoints': [{'line': 4, 'condition': 'i == 99'}]})
            client.request('configurationDone')

            client.wait_event('terminated')
            assert not [m for m in client.stash
                        if m.get('type') == 'event'
                        and m.get('event') == 'stopped'], client.stash
            assert any('i 5' in o for o in client.outputs()), client.outputs()

            client.request('disconnect')
            print('test_dap.py (Alpha 21): never-true condition passed')
        finally:
            client.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def test_conditional_breakpoint_bad_condition():
    """A broken condition surfaces a warning and stops anyway; the
    session survives to termination."""
    workdir, shutil = _alpha18_workdir()
    try:
        prog = workdir / 'cond.niko'
        prog.write_text(COND_PROGRAM)
        prog_p = str(prog.resolve())
        client = TimedClient()
        try:
            client.request('initialize', {'adapterID': 'niko-test'})
            client.wait_event('initialized')
            client.request('launch', {'program': prog_p})
            client.request('setBreakpoints', {
                'source': {'name': 'cond.niko', 'path': prog_p},
                'breakpoints': [{'line': 4, 'condition': 'i =='}]})
            client.request('configurationDone')

            stops = 0
            for _ in range(5):
                stopped = client.wait_event('stopped')
                assert stopped['reason'] == 'breakpoint', stopped
                stops += 1
                if stops == 1:
                    # the failure is surfaced as a plain-English warning,
                    # not a crash
                    assert any('breakpoint condition' in o
                               for o in client.outputs()), client.outputs()
                client.request('continue')
                client.wait_event('continued')
            assert stops == 5, stops
            client.wait_event('terminated')

            client.request('disconnect')
            print('test_dap.py (Alpha 21): bad condition fallback passed')
        finally:
            client.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def test_blank_condition_is_unconditional():
    """An empty-string condition behaves like no condition at all."""
    workdir, shutil = _alpha18_workdir()
    try:
        prog = workdir / 'cond.niko'
        prog.write_text(COND_PROGRAM)
        prog_p = str(prog.resolve())
        client = TimedClient()
        try:
            client.request('initialize', {'adapterID': 'niko-test'})
            client.wait_event('initialized')
            client.request('launch', {'program': prog_p})
            client.request('setBreakpoints', {
                'source': {'name': 'cond.niko', 'path': prog_p},
                'breakpoints': [{'line': 4, 'condition': ''}]})
            client.request('configurationDone')

            for _ in range(5):
                stopped = client.wait_event('stopped')
                assert stopped['reason'] == 'breakpoint', stopped
                client.request('continue')
                client.wait_event('continued')
            client.wait_event('terminated')

            client.request('disconnect')
            print('test_dap.py (Alpha 21): blank condition passed')
        finally:
            client.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


test_conditional_breakpoint()
test_conditional_breakpoint_never_true()
test_conditional_breakpoint_bad_condition()
test_blank_condition_is_unconditional()


def test_use_under_debugger():
    """Alpha 22: breakpoints, stepping, and stack frames inside used files.

    `use` goes through the module pipeline now, so the adapter's
    __use$uK attribution maps frames back to the used file.
    """
    workdir, shutil = _alpha18_workdir()
    try:
        used = workdir / 'used.niko'
        used.write_text('set offset to 5\n'
                        'to bump with x:\n'
                        '    give back x + offset\n')
        main = workdir / 'main.niko'
        main.write_text('use "used.niko"\n'
                        'set r to bump(10)\n'
                        'say r\n'
                        'set r2 to bump(100)\n'
                        'say r2\n')
        used_p, main_p = str(used.resolve()), str(main.resolve())
        client = TimedClient()
        try:
            client.request('initialize', {'adapterID': 'niko-test'})
            client.wait_event('initialized')
            client.request('launch', {'program': main_p})

            # a breakpoint inside the used file verifies and hits with
            # the used file/line on the stack
            bp2 = client.request('setBreakpoints', {
                'source': {'name': 'main.niko', 'path': main_p},
                'breakpoints': [{'line': 2}]})
            assert bp2['breakpoints'] == [{'verified': True, 'line': 2}], bp2
            client.request('configurationDone')

            # stop at the entry call first, then step INTO the used file:
            # the top frame lands in used.niko at the right line
            stopped = client.wait_event('stopped')
            assert stopped['reason'] == 'breakpoint', stopped
            client.request('stepIn')
            stepped = client.wait_event('stopped')
            assert stepped['reason'] == 'step', stepped
            trace = client.request('stackTrace', {'threadId': 1})
            top = trace['stackFrames'][0]
            assert top['name'] == 'bump', top
            assert top['line'] == 3, top
            assert top['source']['path'] == used_p, top
            assert trace['stackFrames'][1]['source']['path'] == main_p, trace

            # locals from the used function's closure are inspectable
            scopes = client.request('scopes', {'frameId': 0})
            variables = client.request(
                'variables',
                {'variablesReference': scopes['scopes'][0]['variablesReference']})
            by_name = {v['name']: v for v in variables['variables']}
            assert by_name['x']['value'] == '10', by_name
            assert by_name['offset']['value'] == '5', by_name

            # step back out: lands in the entry file on the call line.
            # Alpha 23: the entry breakpoint stays armed -- the debugger
            # no longer re-fires it on the post-call STORE (same
            # statement, later instruction), so the step-out completes
            # with reason 'step' instead of a second 'breakpoint' stop.
            client.request('stepOut')
            stepped2 = client.wait_event('stopped')
            assert stepped2['reason'] == 'step', stepped2
            trace3 = client.request('stackTrace', {'threadId': 1})
            assert trace3['stackFrames'][0]['source']['path'] == main_p, trace3
            assert trace3['stackFrames'][0]['line'] == 2, trace3['stackFrames'][0]

            # a breakpoint inside the used file verifies, and on the second
            # call it fires with the used file on top of the stack
            bp = client.request('setBreakpoints', {
                'source': {'name': 'used.niko', 'path': used_p},
                'breakpoints': [{'line': 3}]})
            assert bp['breakpoints'] == [{'verified': True, 'line': 3}], bp

            # continue: the used-file breakpoint fires on the second call,
            # with the used file on top of the stack
            client.request('continue')
            client.wait_event('continued')
            stopped2 = client.wait_event('stopped')
            assert stopped2['reason'] == 'breakpoint', stopped2
            trace2 = client.request('stackTrace', {'threadId': 1})
            assert trace2['stackFrames'][0]['source']['path'] == used_p, trace2
            assert trace2['stackFrames'][0]['line'] == 3, trace2['stackFrames'][0]
            assert trace2['stackFrames'][0]['name'] == 'bump', trace2

            client.request('continue')
            client.wait_event('continued')
            client.wait_event('terminated')
            outs = client.outputs()
            assert any(o.strip() == '15' for o in outs), outs
            assert any(o.strip() == '105' for o in outs), outs

            client.request('disconnect')
            print('test_dap.py (Alpha 22): use under the debugger passed')
        finally:
            client.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


test_use_under_debugger()


def test_no_breakpoint_refire_on_call_line():
    """Alpha 23: a breakpoint on a call line fires once per visit.

    Every instruction of one statement carries the statement's line
    number, so the post-call STORE used to re-fire the armed breakpoint
    when the call returned (after step-in + continue, or after
    step-out). The debugger now treats a *different instruction* on the
    same (frame, line) as the same visit and does not stop again; loop
    iterations re-execute the *same* instruction objects, so they still
    re-fire (covered by the repeat-loop hits in the first test above).
    """
    workdir, shutil = _alpha18_workdir()
    try:
        main = workdir / 'main.niko'
        main.write_text('to bump with x:\n'
                        '    give back x + 1\n'
                        'set r to bump(10)\n'
                        'say r\n')
        main_p = str(main.resolve())
        client = TimedClient()
        try:
            client.request('initialize', {'adapterID': 'niko-test'})
            client.wait_event('initialized')
            client.request('launch', {'program': main_p})
            bp = client.request('setBreakpoints', {
                'source': {'name': 'main.niko', 'path': main_p},
                'breakpoints': [{'line': 3}]})
            assert bp['breakpoints'] == [{'verified': True, 'line': 3}], bp
            client.request('configurationDone')

            # the breakpoint fires once on the call line
            stopped = client.wait_event('stopped')
            assert stopped['reason'] == 'breakpoint', stopped

            # step into the call, then continue: the call returns
            # through the post-call STORE on line 3, which must NOT
            # stop a second time
            client.request('stepIn')
            stepped = client.wait_event('stopped')
            assert stepped['reason'] == 'step', stepped
            trace = client.request('stackTrace', {'threadId': 1})
            assert trace['stackFrames'][0]['name'] == 'bump', trace
            client.request('continue')
            client.wait_event('continued')
            client.wait_event('terminated')
            outs = client.outputs()
            assert any(o.strip() == '11' for o in outs), outs

            client.request('disconnect')
            print('test_dap.py (Alpha 23): no breakpoint re-fire on call line passed')
        finally:
            client.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


test_no_breakpoint_refire_on_call_line()
