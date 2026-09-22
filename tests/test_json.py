#!/usr/bin/env python3
"""Alpha 31: tests for stdlib/json beyond the doc examples.

The doc examples (24 of them) are already executed 3-way + determinism
double-run by tests/test_stdlib2.py. This file covers what doc examples
don't:

1. malformed inputs -- every error names the right 1-based character
   position on VM, WASM, and native
2. \\uXXXX escapes (\\u0041, \\u007f) and the above-U+007F rejection
3. the >= 1e15 number rejection ("number out of range")
4. the 1.0 rendering divergence (VM "1.0" vs WASM/native "1")
5. round-trip parse(stringify(x)) is x: nested, unicode text, empty
   containers, exponents/negatives, duplicate keys, escape-heavy strings
6. deep nesting (50 levels)
7. stringify key insertion order
8. stringify of a function -> "json stringify error" on all backends

WASM/node and native/cc legs skip cleanly when the toolchain is absent,
following the test_stdlib2.py pattern.

KNOWN LIMIT (Alpha 31, see niko2/KNOWN_LIMITATIONS.md): the WASM backend
miscompiles string indexing inside `while yes:` loops on strings
containing multibyte UTF-8, so json.parse of a JSON string with raw
multibyte characters fails on WASM while VM/native succeed. Those cases
assert VM/native success and WASM failure explicitly; if the backend bug
is fixed, the WASM assertions here must be flipped to success.
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

root = pathlib.Path(__file__).resolve().parent
project_root = root.parent
sys.path.insert(0, str(project_root))


def _niko2(*args, cwd, env=None):
    e = dict(os.environ)
    e['PYTHONPATH'] = str(project_root)
    if env:
        e.update(env)
    return subprocess.run([sys.executable, '-m', 'niko2', *args],
                          capture_output=True, text=True, cwd=cwd, env=e)


def _strip_banner(out):
    lines = out.splitlines(keepends=True)
    if lines and lines[0].startswith('✓ built'):
        lines = lines[1:]
    return ''.join(lines)


node = shutil.which('node')
cc = shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')

IMPORT = 'import "stdlib/json.niko" as json\n'


def _niko_strlit(s):
    """Render a Python string as a Niko double-quoted string literal."""
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _run_success(prog, cwd):
    """Returns (vm_out, wasm_out|None, native_out|None); asserts exit 0."""
    p = pathlib.Path(cwd) / 't.niko'
    p.write_text(prog, encoding='utf8')
    r = _niko2('run', 't.niko', cwd=cwd)
    assert r.returncode == 0, f'vm failed:\n{prog}\n{r.stdout}{r.stderr}'
    vm_out = r.stdout
    wasm_out, native_out = None, None
    if node:
        r = _niko2('wasm', 't.niko', '--run', cwd=cwd)
        assert r.returncode == 0, f'wasm failed:\n{prog}\n{r.stdout}{r.stderr}'
        wasm_out = _strip_banner(r.stdout)
    if cc:
        r = _niko2('native', 't.niko', '--run', cwd=cwd)
        assert r.returncode == 0, f'native failed:\n{prog}\n{r.stdout}{r.stderr}'
        native_out = _strip_banner(r.stdout)
    return vm_out, wasm_out, native_out


def _run_all(prog, cwd):
    """Returns [(name, returncode, combined_output)] without asserting."""
    p = pathlib.Path(cwd) / 't.niko'
    p.write_text(prog, encoding='utf8')
    legs = [('vm', _niko2('run', 't.niko', cwd=cwd))]
    if node:
        legs.append(('wasm', _niko2('wasm', 't.niko', '--run', cwd=cwd)))
    else:
        print('SKIP wasm leg: node.js not on PATH')
    if cc:
        legs.append(('native', _niko2('native', 't.niko', '--run', cwd=cwd)))
    else:
        print('SKIP native leg: no C compiler on PATH')
    out = []
    for name, r in legs:
        combined = _strip_banner(r.stdout + r.stderr)
        out.append((name, r.returncode, combined))
    return out


def _assert_3way_equal(prog, cwd, expected, label):
    vm_out, wasm_out, native_out = _run_success(prog, cwd)
    assert vm_out == expected, f'{label} [vm]:\n  want {expected!r}\n  got  {vm_out!r}'
    if wasm_out is not None:
        assert wasm_out == vm_out, f'{label} [wasm != vm]:\n{wasm_out!r}\n{vm_out!r}'
    if native_out is not None:
        assert native_out == vm_out, \
            f'{label} [native != vm]:\n{native_out!r}\n{vm_out!r}'
    print(f'ok: {label}')


with tempfile.TemporaryDirectory() as t:
    # -- 1. malformed inputs: error names the right character position ----
    # (json_text, expected 1-based position, expected message fragment)
    error_cases = [
        ('', 1, 'unexpected end of input'),
        ('{', 2, 'expected a "key"'),
        ('{"a": 1', 8, 'unterminated object'),
        ('[1, 2', 6, 'unterminated array'),
        ('"abc', 5, 'unterminated string'),
        ('{"a": 1,}', 9, 'expected a "key"'),
        ('[1,]', 4, 'unexpected character "]"'),
        ('01', 2, 'unexpected trailing text'),
        ('1.', 3, 'bad number'),
        ('truX', 4, 'expected "true"'),
        ('12 x', 4, 'unexpected trailing text'),
        ('"\\u00e9"', 4, '\\u escapes above U+007F are not supported'),
        ('1e15', 1, 'number out of range'),
        ('-2e15', 1, 'number out of range'),
    ]
    for json_text, pos, msg in error_cases:
        prog = IMPORT + f'say json.parse({_niko_strlit(json_text)})\n'
        for name, code, combined in _run_all(prog, t):
            assert code != 0, f'error case {json_text!r} [{name}] exited 0'
            needle = f'json parse error at character {pos}'
            assert needle in combined, \
                f'error case {json_text!r} [{name}]: {needle!r} not in {combined!r}'
            assert msg in combined, \
                f'error case {json_text!r} [{name}]: {msg!r} not in {combined!r}'
    print(f'ok: {len(error_cases)} malformed inputs name the right character '
          f'position on every backend')

    # -- 2. \\uXXXX escapes --------------------------------------------------
    _assert_3way_equal(
        IMPORT + f'say json.parse({_niko_strlit(chr(34) + chr(92) + "u0041" + chr(34))})\n',
        t, 'A\n', '\\u0041 escape -> A')
    _assert_3way_equal(
        IMPORT + 'say length(json.parse("\\"\\\\u007f\\""))\n',
        t, '1\n', '\\u007f escape has length 1')
    # escapes at the U+007F boundary still work; one past it is an error
    _assert_3way_equal(
        IMPORT + 'say json._parse_unicode("0041", {pos: 1})\n',
        t, 'A\n', '_parse_unicode U+0041')
    _assert_3way_equal(
        IMPORT + 'say length(json._parse_unicode("007f", {pos: 1}))\n',
        t, '1\n', '_parse_unicode U+007F boundary')

    # -- 3. numbers: >= 1e15 rejected, just under is fine -------------------
    _assert_3way_equal(
        IMPORT + 'say json.parse("999999999999999")\n',
        t, '999999999999999\n', '999999999999999 (< 1e15) parses everywhere')
    _assert_3way_equal(
        IMPORT + 'say json.parse("-999999999999999")\n',
        t, '-999999999999999\n', '-999999999999999 parses everywhere')
    _assert_3way_equal(
        IMPORT + 'say json.parse("[1e2, -1.25e-3, 2E+4]")\n',
        t, '[100, -0.00125, 20000]\n', 'exponents parse on every backend')

    # -- 4. 1.0 rendering divergence (documented, per-backend) -------------
    prog = IMPORT + 'say json.stringify(1.0)\nsay json.stringify(2.5)\n'
    vm_out, wasm_out, native_out = _run_success(prog, t)
    assert vm_out == '1.0\n2.5\n', f'1.0 rendering [vm]: {vm_out!r}'
    if wasm_out is not None:
        # KNOWN DIVERGENCE (Alpha 31): each backend's own text() rule --
        # integer-valued floats print "1" on WASM/native, "1.0" on the VM.
        assert wasm_out == '1\n2.5\n', f'1.0 rendering [wasm]: {wasm_out!r}'
    if native_out is not None:
        assert native_out == '1\n2.5\n', f'1.0 rendering [native]: {native_out!r}'
    print('ok: 1.0 renders "1.0" on VM, "1" on WASM/native (documented)')

    # -- 5. round-trip battery: parse(stringify(x)) is x --------------------
    roundtrip_exprs = [
        '{a: [1, -2.5, "x"], b: {c: yes, d: nothing}}',
        '[]',
        '{}',
        '{"k": 1, "k": 2}',   # duplicate keys: last wins (checked below too)
        '"a\\nb\\t\\"q\\"\\\\"',  # escape-heavy string
        '[1, [2, [3, {"deep": [yes]}]]]',
    ]
    for expr in roundtrip_exprs:
        prog = IMPORT + f'say json.parse(json.stringify({expr})) is {expr}\n'
        _assert_3way_equal(prog, t, 'yes\n', f'round-trip {expr[:40]}')
    # numbers through the JSON text forms (Niko has no exponent literals)
    prog = IMPORT + ('say json.parse("1e2") is 100\n'
                     'say json.parse("-1.25e-3") is -0.00125\n'
                     'say json.parse("2E+4") is 20000\n')
    _assert_3way_equal(prog, t, 'yes\nyes\nyes\n', 'round-trip exponents/negatives')
    # duplicate keys: last value wins
    dup_src = _niko_strlit('{"k":1,"k":2}')
    _assert_3way_equal(
        IMPORT + f'say json.parse({dup_src})\n',
        t, '{k: 2}\n', 'duplicate object keys: last wins')
    # unicode text round-trip: VM and native agree; WASM hits the known
    # multibyte-indexing backend bug (see module docstring below).
    prog = IMPORT + 'say json.parse(json.stringify("héllo wörld")) is "héllo wörld"\n'
    legs = _run_all(prog, t)
    by_name = {name: (code, out) for name, code, out in legs}
    assert by_name['vm'] == (0, 'yes\n'), f'unicode round-trip [vm]: {by_name["vm"]!r}'
    if 'native' in by_name:
        assert by_name['native'] == (0, 'yes\n'), \
            f'unicode round-trip [native]: {by_name["native"]!r}'
    if 'wasm' in by_name:
        code, out = by_name['wasm']
        # KNOWN LIMIT (Alpha 31): WASM `while yes:` + string indexing on
        # multibyte text panics/misparses the host -- parse fails here
        # while VM/native succeed. Flip to (0, 'yes\\n') when fixed.
        assert code != 0, f'unicode round-trip [wasm] unexpectedly passed: {out!r}'
        print(f'ok: unicode round-trip fails on WASM as documented '
              f'(known backend limit): {out.strip()[:80]!r}')
    print('ok: round-trip battery green')

    # -- 6. deep nesting (50 levels) ----------------------------------------
    deep = '[' * 50 + ']' * 50
    prog = IMPORT + f'say length(json.stringify(json.parse({_niko_strlit(deep)})))\n'
    _assert_3way_equal(prog, t, '100\n', '50-deep nesting round-trips')

    # -- 7. stringify key insertion order -----------------------------------
    prog = IMPORT + 'say json.stringify(json.parse("{\\"b\\":1,\\"a\\":2,\\"c\\":3}"))\n'
    _assert_3way_equal(prog, t, '{"b":1,"a":2,"c":3}\n', 'key insertion order kept')

    # -- 8. stringify of a function is an error on every backend ------------
    prog = (IMPORT + 'to f:\n    say 1\nsay json.stringify(f)\n')
    for name, code, combined in _run_all(prog, t):
        assert code != 0, f'stringify(fn) [{name}] exited 0'
        assert 'json stringify error' in combined, \
            f'stringify(fn) [{name}]: {combined!r}'
    print('ok: stringify of a function errors on every backend')

    # -- 9. determinism: run the whole battery twice on the VM --------------
    battery = IMPORT + ''.join(
        f'say json.parse(json.stringify({e})) is {e}\n' for e in roundtrip_exprs)
    vm1, _, _ = _run_success(battery, t)
    vm2, _, _ = _run_success(battery, t)
    assert vm1 == vm2 == 'yes\n' * len(roundtrip_exprs), 'determinism'
    print('ok: round-trip battery deterministic across runs')

print('ALL JSON TESTS PASSED')
