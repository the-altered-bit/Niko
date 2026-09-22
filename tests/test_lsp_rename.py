"""Alpha 32: LSP rename refactoring, tested end to end over stdio.

Spawns `python -m niko2 lsp` and speaks real LSP: initialize,
prepareRename / rename, asserting the exact WorkspaceEdit ranges and
the resulting file text -- never just "no error".
"""
import json
import os
import pathlib
import re
import subprocess
import sys

import pytest

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

    def request_raw(self, method, params):
        """Full response message (result or error)."""
        self._seq += 1
        self._send({'jsonrpc': '2.0', 'id': self._seq, 'method': method, 'params': params})
        while True:
            msg = self._read()
            if 'id' in msg and msg.get('id') == self._seq:
                return msg
            self.notifications.append(msg)

    def request(self, method, params):
        msg = self.request_raw(method, params)
        assert 'error' not in msg, f'{method} failed: {msg["error"]}'
        return msg.get('result')

    def notify(self, method, params):
        self._send({'jsonrpc': '2.0', 'method': method, 'params': params})

    def close(self):
        try:
            self.notify('exit', {})
            self.proc.stdin.close()
        except BrokenPipeError:
            pass
        self.proc.wait(timeout=10)


@pytest.fixture(scope='module')
def client():
    c = Client()
    try:
        caps = c.request('initialize', {'processId': None, 'rootUri': None, 'capabilities': {}})
        assert caps['capabilities']['renameProvider'] == {'prepareProvider': True}
        c.notify('initialized', {})
        yield c
        c.request('shutdown', {})
    finally:
        c.close()


_seq = [0]


def open_doc(client, text, suffix='.niko'):
    """didOpen a temp file with `text`; return its uri."""
    _seq[0] += 1
    uri = f'file:///tmp/niko-rename-{os.getpid()}-{_seq[0]}{suffix}'
    client.notify('textDocument/didOpen', {'textDocument': {
        'uri': uri, 'languageId': 'niko', 'version': 1, 'text': text}})
    return uri


def pos_of(text, line, word, nth=0):
    """0-based (line, character) of the nth whole-word `word` on `line`."""
    src = text.splitlines()[line]
    ms = [m for m in re.finditer(r'\b' + re.escape(word) + r'\b', src)]
    assert len(ms) > nth, f'{word!r} not found on line {line}: {src!r}'
    return {'line': line, 'character': ms[nth].start()}


def rename(client, uri, position, new_name):
    return client.request('textDocument/rename', {
        'textDocument': {'uri': uri}, 'position': position,
        'newName': new_name})


def rename_err(client, uri, position, new_name):
    msg = client.request_raw('textDocument/rename', {
        'textDocument': {'uri': uri}, 'position': position,
        'newName': new_name})
    assert 'error' in msg, f'expected an error, got result {msg.get("result")}'
    assert msg['error']['code'] == -32602, msg['error']
    return msg['error']['message']


def prepare(client, uri, position):
    return client.request('textDocument/prepareRename', {
        'textDocument': {'uri': uri}, 'position': position})


def tuples(changes, uri):
    return sorted(
        (e['range']['start']['line'], e['range']['start']['character'],
         e['range']['end']['character'], e['newText'])
        for e in changes[uri])


def apply_edits(text, edits):
    lines = text.split('\n')
    offs, acc = [], 0
    for ln in lines:
        offs.append(acc)
        acc += len(ln) + 1

    def at(p):
        return offs[p['line']] + p['character']

    spans = sorted(((at(e['range']['start']), at(e['range']['end']),
                     e['newText']) for e in edits), reverse=True)
    for s, e_, nt in spans:
        text = text[:s] + nt + text[e_:]
    return text


# --- in-file renames -------------------------------------------------------

def test_local_rename_with_closure_capture(client):
    src = ('set total to 0\n'
           'add 5 to total\n'
           'say total\n'
           '\n'
           'to bump:\n'
           '    add 1 to total\n')
    uri = open_doc(client, src)
    we = rename(client, uri, pos_of(src, 0, 'total'), 'count')
    assert set(we['changes']) == {uri}
    assert tuples(we['changes'], uri) == [
        (0, 4, 9, 'count'), (1, 9, 14, 'count'),
        (2, 4, 9, 'count'), (5, 13, 18, 'count')]
    assert apply_edits(src, we['changes'][uri]) == (
        'set count to 0\n'
        'add 5 to count\n'
        'say count\n'
        '\n'
        'to bump:\n'
        '    add 1 to count\n')


def test_parameter_rename(client):
    src = ('to greet with who:\n'
           '    say "hi", who\n'
           '\n'
           'greet("Casper")\n')
    uri = open_doc(client, src)
    we = rename(client, uri, pos_of(src, 0, 'who'), 'visitor')
    assert tuples(we['changes'], uri) == [(0, 14, 17, 'visitor'),
                                          (1, 14, 17, 'visitor')]
    assert apply_edits(src, we['changes'][uri]) == (
        'to greet with visitor:\n'
        '    say "hi", visitor\n'
        '\n'
        'greet("Casper")\n')


def test_shadowed_inner_renames_only_inner_refs(client):
    src = ('set x to 1\n'
           'to f:\n'
           '    set x to 2\n'
           '    say x\n'
           'say x\n')
    uri = open_doc(client, src)
    we = rename(client, uri, pos_of(src, 2, 'x'), 'y')
    assert tuples(we['changes'], uri) == [(2, 8, 9, 'y'), (3, 8, 9, 'y')]
    assert apply_edits(src, we['changes'][uri]) == (
        'set x to 1\n'
        'to f:\n'
        '    set y to 2\n'
        '    say y\n'
        'say x\n')


def test_outer_rename_skips_inner_shadow(client):
    src = ('set x to 1\n'
           'to f:\n'
           '    set x to 2\n'
           '    say x\n'
           'say x\n')
    uri = open_doc(client, src)
    we = rename(client, uri, pos_of(src, 0, 'x'), 'z')
    assert tuples(we['changes'], uri) == [(0, 4, 5, 'z'), (4, 4, 5, 'z')]
    assert apply_edits(src, we['changes'][uri]) == (
        'set z to 1\n'
        'to f:\n'
        '    set x to 2\n'
        '    say x\n'
        'say z\n')


def test_top_level_function_rename_with_call_sites(client):
    src = ('to greet with who:\n'
           '    say who\n'
           '\n'
           'greet("a")\n'
           'greet("b")\n')
    uri = open_doc(client, src)
    we = rename(client, uri, pos_of(src, 3, 'greet'), 'welcome')
    assert set(we['changes']) == {uri}
    assert tuples(we['changes'], uri) == [(0, 3, 8, 'welcome'),
                                         (3, 0, 5, 'welcome'),
                                         (4, 0, 5, 'welcome')]
    assert apply_edits(src, we['changes'][uri]) == (
        'to welcome with who:\n'
        '    say who\n'
        '\n'
        'welcome("a")\n'
        'welcome("b")\n')


def test_loop_variable_rename(client):
    src = ('for each elem in [1, 2]:\n'
           '    say elem\n')
    uri = open_doc(client, src)
    we = rename(client, uri, pos_of(src, 0, 'elem'), 'thing')
    assert tuples(we['changes'], uri) == [(0, 9, 13, 'thing'),
                                          (1, 8, 12, 'thing')]
    assert apply_edits(src, we['changes'][uri]) == (
        'for each thing in [1, 2]:\n'
        '    say thing\n')


def test_ask_binding_rename(client):
    src = ('ask "your name" into who\n'
           'say who\n')
    uri = open_doc(client, src)
    we = rename(client, uri, pos_of(src, 1, 'who'), 'visitor')
    assert tuples(we['changes'], uri) == [(0, 21, 24, 'visitor'),
                                          (1, 4, 7, 'visitor')]


def test_alias_rename_is_in_file_only(client):
    src = ('import "lib/math.niko" as m\n'
           'say m.add(1, 2)\n'
           'say m.tau\n')
    uri = open_doc(client, src)
    we = rename(client, uri, pos_of(src, 0, 'm'), 'mm')
    # the module file is never touched
    assert set(we['changes']) == {uri}
    assert tuples(we['changes'], uri) == [(0, 26, 27, 'mm'),
                                         (1, 4, 5, 'mm'),
                                         (2, 4, 5, 'mm')]
    assert apply_edits(src, we['changes'][uri]) == (
        'import "lib/math.niko" as mm\n'
        'say mm.add(1, 2)\n'
        'say mm.tau\n')


def test_rename_to_same_name_is_noop(client):
    src = 'set x to 1\nsay x\n'
    uri = open_doc(client, src)
    we = rename(client, uri, pos_of(src, 0, 'x'), 'x')
    assert we == {'changes': {}}


def test_record_key_and_field_not_renamed(client):
    src = ('set name to "x"\n'
           'set r to {name: 1}\n'
           'say r.name\n'
           'say name\n')
    uri = open_doc(client, src)
    we = rename(client, uri, pos_of(src, 0, 'name'), 'nm')
    # the {name: 1} key and the .name attribute stay; only the
    # variable's own occurrences rename
    assert tuples(we['changes'], uri) == [(0, 4, 8, 'nm'), (3, 4, 8, 'nm')]
    assert apply_edits(src, we['changes'][uri]) == (
        'set nm to "x"\n'
        'set r to {name: 1}\n'
        'say r.name\n'
        'say nm\n')


# --- prepareRename ----------------------------------------------------------

def test_prepare_rename_returns_range_and_placeholder(client):
    src = 'set total to 0\nsay total\n'
    uri = open_doc(client, src)
    pr = prepare(client, uri, pos_of(src, 1, 'total'))
    assert pr == {'range': {'start': {'line': 1, 'character': 4},
                            'end': {'line': 1, 'character': 9}},
                  'placeholder': 'total'}


def test_prepare_rename_null_on_string_keyword_builtin(client):
    src = 'say "hello"\nset x to 1\nsay upper(x)\n'
    uri = open_doc(client, src)
    # inside the string
    assert prepare(client, uri, {'line': 0, 'character': 7}) is None
    # on the `set` keyword
    assert prepare(client, uri, {'line': 1, 'character': 1}) is None
    # on the `upper` builtin
    assert prepare(client, uri, pos_of(src, 2, 'upper')) is None


# --- refusals -----------------------------------------------------------------

def test_refuse_builtin_new_name(client):
    src = 'set x to 1\nsay x\n'
    uri = open_doc(client, src)
    msg = rename_err(client, uri, pos_of(src, 0, 'x'), 'upper')
    assert 'builtin' in msg and 'upper' in msg, msg


def test_refuse_keyword_new_name(client):
    src = 'set x to 1\n'
    uri = open_doc(client, src)
    msg = rename_err(client, uri, pos_of(src, 0, 'x'), 'while')
    assert 'keyword' in msg and 'while' in msg, msg


def test_refuse_illegal_new_names(client):
    src = 'set x to 1\n'
    uri = open_doc(client, src)
    for bad in ('123abc', 'foo-bar', ''):
        msg = rename_err(client, uri, pos_of(src, 0, 'x'), bad)
        assert 'not a valid Niko name' in msg, (bad, msg)


def test_refuse_collision_with_same_scope_binding(client):
    src = ('to f:\n'
           '    set x to 1\n'
           '    set y to 2\n'
           '    say x\n')
    uri = open_doc(client, src)
    msg = rename_err(client, uri, pos_of(src, 1, 'x'), 'y')
    assert 'already defined' in msg, msg


def test_refuse_collision_where_nested_binding_would_capture(client):
    src = ('set x to 1\n'
           'to f with y:\n'
           '    say x\n')
    uri = open_doc(client, src)
    # renaming x -> y would make `say x` resolve to f's parameter y
    msg = rename_err(client, uri, pos_of(src, 0, 'x'), 'y')
    assert 'clash' in msg, msg


def test_refuse_cursor_in_string(client):
    src = 'say "hello x"\nset x to 1\n'
    uri = open_doc(client, src)
    msg = rename_err(client, uri, {'line': 0, 'character': 10}, 'y')
    assert 'nothing to rename' in msg, msg


def test_refuse_cursor_on_import_path_string(client):
    src = 'import "lib/math.niko" as m\nsay 1\n'
    uri = open_doc(client, src)
    msg = rename_err(client, uri, {'line': 0, 'character': 12}, 'm2')
    assert 'nothing to rename' in msg, msg


def test_refuse_cursor_on_keyword(client):
    src = 'set x to 1\n'
    uri = open_doc(client, src)
    msg = rename_err(client, uri, {'line': 0, 'character': 1}, 'y')
    assert 'keyword' in msg and '"set"' in msg, msg


def test_refuse_cursor_on_builtin(client):
    src = 'say upper("a")\n'
    uri = open_doc(client, src)
    msg = rename_err(client, uri, pos_of(src, 0, 'upper'), 'y')
    assert 'builtin' in msg and '"upper"' in msg, msg


def test_refuse_record_field(client):
    src = 'set rec to {field: 1}\nsay rec.field\n'
    uri = open_doc(client, src)
    msg = rename_err(client, uri, pos_of(src, 1, 'field'), 'f2')
    assert 'not a renameable name' in msg, msg


# --- cross-file ---------------------------------------------------------------

@pytest.fixture()
def proj(tmp_path):
    (tmp_path / 'lib').mkdir()
    (tmp_path / 'niko.lock').write_text('{}', encoding='utf8')
    (tmp_path / 'lib' / 'math.niko').write_text(
        'to add with a, b:\n'
        '    give back a + b\n'
        '\n'
        'set tau to 6.28\n', encoding='utf8')
    (tmp_path / 'main.niko').write_text(
        'import "lib/math.niko" as m\n'
        'say m.add(1, 2)\n'
        'say m.tau\n', encoding='utf8')
    (tmp_path / 'other.niko').write_text(
        'import "lib/math.niko" as calc\n'
        'say calc.add(3, 4)\n', encoding='utf8')
    (tmp_path / 'uses.niko').write_text(
        'use "lib/math.niko"\n'
        'say add(5, 6)\n', encoding='utf8')
    return tmp_path


def open_proj_file(client, path):
    uri = path.as_uri()
    client.notify('textDocument/didOpen', {'textDocument': {
        'uri': uri, 'languageId': 'niko', 'version': 1,
        'text': path.read_text(encoding='utf8')}})
    return uri


def test_cross_file_export_rename_from_importer(client, proj):
    math_uri = open_proj_file(client, proj / 'lib' / 'math.niko')
    main_uri = open_proj_file(client, proj / 'main.niko')
    other_uri = open_proj_file(client, proj / 'other.niko')
    uses_uri = open_proj_file(client, proj / 'uses.niko')

    main_src = (proj / 'main.niko').read_text(encoding='utf8')
    we = rename(client, main_uri, pos_of(main_src, 1, 'add'), 'total')
    changes = we['changes']
    assert set(changes) == {math_uri, main_uri, other_uri, uses_uri}, \
        set(changes)

    # the definition file: `to add` becomes `to total`
    assert tuples(changes, math_uri) == [(0, 3, 6, 'total')]
    # each importer keeps its own alias spelling
    assert tuples(changes, main_uri) == [(1, 6, 9, 'total')]
    assert tuples(changes, other_uri) == [(1, 9, 12, 'total')]
    # the `use` file's bare-name reference renames too
    assert tuples(changes, uses_uri) == [(1, 4, 7, 'total')]

    assert apply_edits((proj / 'lib' / 'math.niko').read_text(encoding='utf8'),
                       changes[math_uri]) == (
        'to total with a, b:\n'
        '    give back a + b\n'
        '\n'
        'set tau to 6.28\n')
    assert apply_edits(main_src, changes[main_uri]) == (
        'import "lib/math.niko" as m\n'
        'say m.total(1, 2)\n'
        'say m.tau\n')
    assert apply_edits((proj / 'other.niko').read_text(encoding='utf8'),
                       changes[other_uri]) == (
        'import "lib/math.niko" as calc\n'
        'say calc.total(3, 4)\n')
    assert apply_edits((proj / 'uses.niko').read_text(encoding='utf8'),
                       changes[uses_uri]) == (
        'use "lib/math.niko"\n'
        'say total(5, 6)\n')


def test_cross_file_finds_unopened_importer_on_disk(client, tmp_path):
    # only the definition file is open; the importer is discovered by
    # the bounded project-tree walk (niko.lock marks the project root)
    (tmp_path / 'niko.lock').write_text('{}', encoding='utf8')
    (tmp_path / 'lib').mkdir()
    (tmp_path / 'lib' / 'math.niko').write_text(
        'to add with a, b:\n'
        '    give back a + b\n', encoding='utf8')
    (tmp_path / 'app.niko').write_text(
        'import "lib/math.niko" as m\n'
        'say m.add(9, 9)\n', encoding='utf8')
    math_uri = open_proj_file(client, tmp_path / 'lib' / 'math.niko')
    app_uri = (tmp_path / 'app.niko').as_uri()

    math_src = (tmp_path / 'lib' / 'math.niko').read_text(encoding='utf8')
    we = rename(client, math_uri, pos_of(math_src, 0, 'add'), 'total')
    changes = we['changes']
    assert set(changes) == {math_uri, app_uri}, set(changes)
    assert tuples(changes, math_uri) == [(0, 3, 6, 'total')]
    assert tuples(changes, app_uri) == [(1, 6, 9, 'total')]


def test_rename_use_name_from_using_file_is_refused(client, proj):
    uses_uri = open_proj_file(client, proj / 'uses.niko')
    uses_src = (proj / 'uses.niko').read_text(encoding='utf8')
    msg = rename_err(client, uses_uri, pos_of(uses_src, 1, 'add'), 'total')
    assert '`use`' in msg, msg


def test_cross_file_skips_shadowed_alias_line(client, proj):
    # `m` is shadowed by a local inside f: that line's `m.add` is not
    # the module attribute, so it must not be renamed
    math_uri = open_proj_file(client, proj / 'lib' / 'math.niko')
    imp_src = ('import "lib/math.niko" as m\n'
               'to f:\n'
               '    set m to 5\n'
               '    say m.add(1, 2)\n'
               'say m.add(3, 4)\n')
    imp = proj / 'shadowed.niko'
    imp.write_text(imp_src, encoding='utf8')
    imp_uri = open_proj_file(client, imp)
    math_src = (proj / 'lib' / 'math.niko').read_text(encoding='utf8')
    we = rename(client, math_uri, pos_of(math_src, 0, 'add'), 'total')
    assert tuples(we['changes'], imp_uri) == [(4, 6, 9, 'total')]


def test_rename_refused_when_file_has_errors(client):
    uri = open_doc(client, 'set x to\n')
    msg = rename_err(client, uri, {'line': 0, 'character': 4}, 'y')
    assert 'errors' in msg, msg


def test_unsaved_buffer_rename(client):
    src = 'set q to 1\nsay q\n'
    uri = open_doc(client, src)
    changed = 'set q to 1\nsay q\nsay q + q\n'
    client.notify('textDocument/didChange', {
        'textDocument': {'uri': uri, 'version': 2},
        'contentChanges': [{'text': changed}]})
    we = rename(client, uri, pos_of(changed, 0, 'q'), 'qq')
    assert tuples(we['changes'], uri) == [(0, 4, 5, 'qq'), (1, 4, 5, 'qq'),
                                          (2, 4, 5, 'qq'), (2, 8, 9, 'qq')]
    assert apply_edits(changed, we['changes'][uri]) == (
        'set qq to 1\nsay qq\nsay qq + qq\n')
