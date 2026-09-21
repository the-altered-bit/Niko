"""Alpha 7: the DAP debug adapter, tested end to end over stdio.

Spawns `python -m niko2 debug` and speaks real DAP against a small
program: launch, set a breakpoint, hit it three times, inspect the
stack and the `total` variable, step once, continue to termination.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

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
