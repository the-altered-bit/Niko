"""Alpha 7: the LSP language server, tested end to end over stdio.

Spawns `python -m niko2 lsp` and speaks real LSP: initialize, didOpen
(expecting publishDiagnostics with the type error), completion, hover,
definition, formatting, shutdown.
"""
import json
import os
import pathlib
import subprocess
import sys

root = pathlib.Path(__file__).resolve().parent.parent


class Client:
    def __init__(self):
        env = dict(os.environ, PYTHONPATH=str(root))
        self.proc = subprocess.Popen(
            [sys.executable, '-m', 'niko2', 'lsp'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            cwd=root, env=env,
        )
        self._seq = 0
        self.notifications = []

    def _send(self, obj):
        body = json.dumps(obj).encode('utf-8')
        self.proc.stdin.write(b'Content-Length: ' + str(len(body)).encode() + b'\r\n\r\n' + body)
        self.proc.stdin.flush()

    def _read(self):
        headers = {}
        while True:
            line = self.proc.stdout.readline()
            assert line, 'server closed stdout'
            line = line.strip()
            if not line:
                break
            k, v = line.split(b':', 1)
            headers[k.strip().lower()] = v.strip()
        body = self.proc.stdout.read(int(headers[b'content-length']))
        return json.loads(body)

    def request(self, method, params):
        self._seq += 1
        self._send({'jsonrpc': '2.0', 'id': self._seq, 'method': method, 'params': params})
        while True:
            msg = self._read()
            if 'id' in msg and msg.get('id') == self._seq:
                assert 'error' not in msg, f'{method} failed: {msg["error"]}'
                return msg.get('result')
            self.notifications.append(msg)

    def notify(self, method, params):
        self._send({'jsonrpc': '2.0', 'method': method, 'params': params})

    def drain_notifications(self, method):
        out = []
        while self.notifications:
            msg = self.notifications.pop(0)
            if msg.get('method') == method:
                out.append(msg)
        return out

    def close(self):
        try:
            self.notify('exit', {})
            self.proc.stdin.close()
        except BrokenPipeError:
            pass
        self.proc.wait(timeout=10)


SRC = '''set total: number to 0
set name to "Niko"
say upper(name)

to greet with who:
    say "hi", who

greet(name)
set bad: number to "oops"
'''

client = Client()
try:
    caps = client.request('initialize', {'processId': None, 'rootUri': None, 'capabilities': {}})
    assert caps['capabilities']['hoverProvider'] is True
    assert caps['capabilities']['definitionProvider'] is True
    client.notify('initialized', {})

    uri = 'file:///tmp/lsp-test.niko'
    client.notify('textDocument/didOpen', {'textDocument': {'uri': uri, 'languageId': 'niko', 'version': 1, 'text': SRC}})

    # pull messages until we see publishDiagnostics
    diags = None
    for _ in range(20):
        msg = client._read()
        if msg.get('method') == 'textDocument/publishDiagnostics':
            diags = msg['params']['diagnostics']
            break
        client.notifications.append(msg)
    assert diags is not None, 'no publishDiagnostics received'
    assert len(diags) == 1, f'expected 1 diagnostic, got {diags}'
    assert diags[0]['range']['start']['line'] == 8, diags[0]  # 0-based line of `set bad...`
    assert 'number' in diags[0]['message'] and 'text' in diags[0]['message'], diags[0]['message']

    # completion contains keywords, builtins, and user names
    items = client.request('textDocument/completion', {
        'textDocument': {'uri': uri}, 'position': {'line': 0, 'character': 0}})
    labels = {i['label'] for i in items}
    assert 'match' in labels and 'otherwise' in labels
    assert 'try_read_file' in labels and 'unwrap' in labels
    assert 'total' in labels and 'greet' in labels

    # hover on a builtin shows its doc
    hov = client.request('textDocument/hover', {
        'textDocument': {'uri': uri}, 'position': {'line': 2, 'character': 5}})
    assert hov is not None and 'upper' in hov['contents']['value'], hov

    # hover on a user name shows its inferred type
    hov = client.request('textDocument/hover', {
        'textDocument': {'uri': uri}, 'position': {'line': 0, 'character': 5}})
    assert hov is not None and 'number' in hov['contents']['value'], hov

    # definition jumps to the `to greet` line (0-based line 4)
    loc = client.request('textDocument/definition', {
        'textDocument': {'uri': uri}, 'position': {'line': 7, 'character': 2}})
    assert loc is not None and loc['range']['start']['line'] == 4, loc

    # formatting returns a full-document edit that parses
    edits = client.request('textDocument/formatting', {
        'textDocument': {'uri': uri}, 'options': {}})
    assert edits and edits[0]['newText'].startswith('set total: number to 0')

    # didChange with fixed source clears diagnostics
    fixed = SRC.replace('set bad: number to "oops"', 'set bad: number to 1')
    client.notify('textDocument/didChange', {
        'textDocument': {'uri': uri, 'version': 2},
        'contentChanges': [{'text': fixed}]})
    diags = None
    for _ in range(20):
        msg = client._read()
        if msg.get('method') == 'textDocument/publishDiagnostics':
            diags = msg['params']['diagnostics']
            break
        client.notifications.append(msg)
    assert diags == [], f'expected no diagnostics, got {diags}'

    client.request('shutdown', {})
    print('test_lsp.py: all assertions passed')
finally:
    client.close()
