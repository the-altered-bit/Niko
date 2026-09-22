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


# --- Alpha 17: cross-file go-to-definition ---------------------------------
import shutil
import tempfile

fixture = tempfile.mkdtemp(prefix='niko-lsp-xfile-')
try:
    proj = pathlib.Path(fixture) / 'proj'
    (proj / 'lib').mkdir(parents=True)
    (proj / 'lib' / 'math.niko').write_text(
        '# add(a, b)\n'
        '# Add two numbers together.\n'
        'to add with a, b:\n'
        '    give back a + b\n'
        '\n'
        '# tau: the circle constant, 2 * pi.\n'
        'set tau to 6.28\n',
        encoding='utf8')
    main_src = (
        'import "lib/math.niko" as m\n'
        'import "pkg:hello/main.niko" as h\n'
        'import "missing.niko" as n\n'
        '\n'
        'say m.add(1, 2)\n'
        'say m.tau\n'
        'say h.greet("Casper")\n'
        'say n.anything\n'
    )
    (proj / 'main.niko').write_text(main_src, encoding='utf8')
    pkg = pathlib.Path(fixture) / 'pkgcache' / 'hello-1.0.0'
    pkg.mkdir(parents=True)
    (pkg / 'main.niko').write_text(
        'import "./util.niko" as u\n\nto greet with who:\n    give back u.shout(who)\n',
        encoding='utf8')
    (pkg / 'util.niko').write_text(
        'to shout with t:\n    give back upper(t)\n', encoding='utf8')
    (proj / 'niko.lock').write_text(
        json.dumps({'packages': {'hello': {'version': '1.0.0',
                                           'source': 'test-fixture'}}}),
        encoding='utf8')

    old_cache = os.environ.get('NIKO_PKG_CACHE')
    os.environ['NIKO_PKG_CACHE'] = str(pathlib.Path(fixture) / 'pkgcache')
    client2 = Client()
    try:
        caps = client2.request('initialize', {'processId': None, 'rootUri': None, 'capabilities': {}})
        assert caps['capabilities']['definitionProvider'] is True
        client2.notify('initialized', {})

        main_uri = (proj / 'main.niko').as_uri()
        math_uri = (proj / 'lib' / 'math.niko').as_uri()
        hello_uri = (pkg / 'main.niko').as_uri()
        util_uri = (pkg / 'util.niko').as_uri()
        client2.notify('textDocument/didOpen', {'textDocument': {
            'uri': main_uri, 'languageId': 'niko', 'version': 1,
            'text': main_src}})
        # pull messages until we see publishDiagnostics (an unresolvable
        # import still types as map, so expect zero diagnostics)
        diags = None
        for _ in range(20):
            msg = client2._read()
            if msg.get('method') == 'textDocument/publishDiagnostics':
                diags = msg['params']['diagnostics']
                break
            client2.notifications.append(msg)
        assert diags == [], f'expected no diagnostics, got {diags}'

        def goto(uri, line, char):
            return client2.request('textDocument/definition', {
                'textDocument': {'uri': uri},
                'position': {'line': line, 'character': char}})

        # the import string itself -> the module file
        loc = goto(main_uri, 0, 10)
        assert loc is not None and loc['uri'] == math_uri \
            and loc['range']['start']['line'] == 0, loc

        # m.add -> `to add` in the module file (0-based line 2)
        loc = goto(main_uri, 4, 7)
        assert loc is not None and loc['uri'] == math_uri \
            and loc['range']['start']['line'] == 2, loc

        # m.tau -> `set tau` in the module file (0-based line 6)
        loc = goto(main_uri, 5, 7)
        assert loc is not None and loc['uri'] == math_uri \
            and loc['range']['start']['line'] == 6, loc

        # cursor on the alias itself -> the import line (single-file)
        loc = goto(main_uri, 4, 4)
        assert loc is not None and loc['uri'] == main_uri \
            and loc['range']['start']['line'] == 0, loc

        # pkg: import (lockfile pin) -> the cached package file
        loc = goto(main_uri, 6, 7)
        assert loc is not None and loc['uri'] == hello_uri \
            and loc['range']['start']['line'] == 2, loc

        # relative ./ import inside the package -> the sibling file
        client2.notify('textDocument/didOpen', {'textDocument': {
            'uri': hello_uri, 'languageId': 'niko', 'version': 1,
            'text': (pkg / 'main.niko').read_text(encoding='utf8')}})
        loc = goto(hello_uri, 3, 18)
        assert loc is not None and loc['uri'] == util_uri \
            and loc['range']['start']['line'] == 0, loc

        # missing module: no crash, null result
        loc = goto(main_uri, 2, 10)
        assert loc is None, loc
        loc = goto(main_uri, 7, 6)
        assert loc is None, loc

        # --- Alpha 21: cross-file hover --------------------------------
        def hover(uri, line, char):
            return client2.request('textDocument/hover', {
                'textDocument': {'uri': uri},
                'position': {'line': line, 'character': char}})

        # hovering `add` in `m.add(...)` shows the signature and doc
        # comment from the module file
        hov = hover(main_uri, 4, 7)
        assert hov is not None, 'no hover for m.add'
        val = hov['contents']['value']
        assert 'add(a, b)' in val, val
        assert 'Add two numbers together.' in val, val

        # hovering `tau` in `m.tau` shows its inferred type and doc
        hov = hover(main_uri, 5, 7)
        assert hov is not None, 'no hover for m.tau'
        val = hov['contents']['value']
        assert '**tau**' in val and 'number' in val, val
        assert 'circle constant' in val, val

        # pkg: import hover works through the lockfile pin
        hov = hover(main_uri, 6, 7)
        assert hov is not None, 'no hover for h.greet'
        assert 'greet(who)' in hov['contents']['value'], hov

        # hovering the alias itself still resolves locally (module value)
        hov = hover(main_uri, 4, 4)
        assert hov is not None and '**m**' in hov['contents']['value'], hov

        # missing module: no crash, null hover
        assert hover(main_uri, 7, 6) is None

        client2.request('shutdown', {})
        print('test_lsp.py (Alpha 17): cross-file definition assertions passed')
        print('test_lsp.py (Alpha 21): cross-file hover assertions passed')
    finally:
        client2.close()
        if old_cache is None:
            os.environ.pop('NIKO_PKG_CACHE', None)
        else:
            os.environ['NIKO_PKG_CACHE'] = old_cache
finally:
    shutil.rmtree(fixture, ignore_errors=True)


# --- Alpha 22: cross-file go-to-definition + hover through `use` ------------
fixture22 = tempfile.mkdtemp(prefix='niko-lsp-use-')
try:
    proj = pathlib.Path(fixture22) / 'proj'
    (proj / 'lib').mkdir(parents=True)
    (proj / 'lib' / 'helper.niko').write_text(
        '# double(x)\n'
        '# Double a number.\n'
        'to double with x:\n'
        '    give back x * 2\n'
        '\n'
        '# base: the shared base value.\n'
        'set base to 21\n',
        encoding='utf8')
    main_src = (
        'use "lib/helper.niko"\n'
        'use "missing.niko"\n'
        'set base to 999\n'
        'say double(base)\n'
        'say base\n'
    )
    (proj / 'main.niko').write_text(main_src, encoding='utf8')

    client3 = Client()
    try:
        caps = client3.request('initialize', {'processId': None, 'rootUri': None, 'capabilities': {}})
        assert caps['capabilities']['definitionProvider'] is True
        client3.notify('initialized', {})

        main_uri = (proj / 'main.niko').as_uri()
        helper_uri = (proj / 'lib' / 'helper.niko').as_uri()
        client3.notify('textDocument/didOpen', {'textDocument': {
            'uri': main_uri, 'languageId': 'niko', 'version': 1,
            'text': main_src}})
        diags = None
        for _ in range(20):
            msg = client3._read()
            if msg.get('method') == 'textDocument/publishDiagnostics':
                diags = msg['params']['diagnostics']
                break
            client3.notifications.append(msg)
        # Known Alpha 22 gap: editor diagnostics run the plain checker,
        # which doesn't know `use`-merged names -- `double` is flagged
        # unknown even though it resolves at compile time.
        assert diags is not None, 'no publishDiagnostics arrived'
        assert any('unknown name "double"' in d['message'] for d in diags), diags

        def goto(uri, line, char):
            return client3.request('textDocument/definition', {
                'textDocument': {'uri': uri},
                'position': {'line': line, 'character': char}})

        def hover(uri, line, char):
            return client3.request('textDocument/hover', {
                'textDocument': {'uri': uri},
                'position': {'line': line, 'character': char}})

        # the use's path string -> the used file itself
        loc = goto(main_uri, 0, 8)
        assert loc is not None and loc['uri'] == helper_uri \
            and loc['range']['start']['line'] == 0, loc

        # `double` (merged in by use) -> `to double` in the used file
        # (0-based line 2)
        loc = goto(main_uri, 3, 5)
        assert loc is not None and loc['uri'] == helper_uri \
            and loc['range']['start']['line'] == 2, loc

        # the entry's own `set base` wins over the used file's `set base`
        loc = goto(main_uri, 4, 5)
        assert loc is not None and loc['uri'] == main_uri \
            and loc['range']['start']['line'] == 2, loc

        # missing used file: no crash, null result
        assert goto(main_uri, 1, 8) is None

        # hover on `double` shows the signature and doc comment from the
        # used file
        hov = hover(main_uri, 3, 5)
        assert hov is not None, 'no hover for used name double'
        val = hov['contents']['value']
        assert 'double(x)' in val, val
        assert 'Double a number.' in val, val

        # hover on the entry's own `base` stays local (entry wins)
        hov = hover(main_uri, 4, 5)
        assert hov is not None and '**base**' in hov['contents']['value'], hov

        client3.request('shutdown', {})
        print('test_lsp.py (Alpha 22): use definition + hover assertions passed')
    finally:
        client3.close()
finally:
    shutil.rmtree(fixture22, ignore_errors=True)
