#!/usr/bin/env python3
"""Alpha 33: WASM multibyte string indexing — 3-way differential.

Root cause (fixed in niko2/backends/wasm.py `_b_item_of`): the text branch
overwrote the byte-length local `n` with the char count from `utf8_len`,
then passed `n` as the *byte* length argument to `utf8_byte_offset`.
For pure-ASCII text the two are equal, so nothing showed; for multibyte
text the byte scan stopped early at the char-count bound and the end
offset of the final character(s) came back truncated -- `item_of` on the
last character returned "" instead of the character. In a `while yes:`
scan-to-terminator loop (exactly what json.niko's string parser does)
the terminator never matched, the loop ran past the end, and the host
panicked with "string index out of range".

All happy-path cases run 3-way differential (VM/WASM/native) with
byte-identical output. The out-of-range error case asserts each backend
fails loudly with "string index out of range".
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


def _niko2(*args, cwd):
    env = dict(os.environ, PYTHONPATH=str(project_root))
    return subprocess.run(
        [sys.executable, "-m", "niko2", *args],
        capture_output=True, text=True, cwd=cwd, env=env)


def _strip_banner(out):
    lines = out.splitlines(keepends=True)
    if lines and lines[0].startswith('✓ built'):
        lines = lines[1:]
    return ''.join(lines)


# (name, source, expected stdout); the VM is canonical, WASM and native
# must print byte-identical output.
CASES = [
    # --- item_of on multibyte text, every position -----------------------
    ('item_first', 'say item_of(1, "héllo")\n', 'h\n'),
    ('item_multibyte_mid', 'say item_of(2, "héllo")\n', 'é\n'),
    ('item_mid', 'say item_of(3, "héllo")\n', 'l\n'),
    ('item_last', 'say item_of(5, "héllo")\n', 'o\n'),
    ('item_negative', 'say item_of(-1, "héllo")\n', 'l\n'),
    ('item_negative_mid', 'say item_of(-4, "héllo")\n', 'h\n'),
    # 4-byte character (U+1F600) between ASCII
    ('item_emoji_first', 'say item_of(1, "a😀b")\n', 'a\n'),
    ('item_emoji', 'say item_of(2, "a😀b")\n', '😀\n'),
    ('item_emoji_last', 'say item_of(3, "a😀b")\n', 'b\n'),
    # all-multibyte string, last char (the exact shape that returned "")
    ('item_all_mb_last', 'say item_of(3, "αβγ")\n', 'γ\n'),
    # --- length counts characters, not bytes ------------------------------
    ('length_multibyte', 'say length("héllo")\n', '5\n'),
    ('length_emoji', 'say length("a😀b")\n', '3\n'),
    ('length_all_mb', 'say length("αβγ")\n', '3\n'),
    # --- while loops over multibyte text ----------------------------------
    ('while_bounded_walk', '''\
to walk with s: text -> text:
    set out to ""
    set i to 1
    while i < 6:
        set out to out + item_of(i, s)
        set i to i + 1
    give back out
say walk("héllo")
''', 'héllo\n'),
    # the Alpha 31 repro shape: while yes: scan to a terminator
    ('while_yes_terminator', '''\
to until_quote with s: text, pos: number -> text:
    set out to ""
    while yes:
        set c to item_of(pos, s)
        if c is "\\"":
            give back out
        otherwise:
            set out to out + c
            set pos to pos + 1
say until_quote("\\"héllo\\"", 2)
''', 'héllo\n'),
    # --- bulk text ops on multibyte input ---------------------------------
    ('reversed_mb', 'say reversed("héllo")\n', '["o", "l", "l", "é", "h"]\n'),
    ('concat_mb', 'say "hé" + "llo"\nsay "wör" + "ld"\n', 'héllo\nwörld\n'),
    ('split_join_mb',
     'say join(split("héllo wörld", " "), "-")\n', 'héllo-wörld\n'),
    ('upper_ascii_still', 'say upper("abc")\n', 'ABC\n'),
]

node = shutil.which('node')
cc = shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')

for name, src, want in CASES:
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        (tmp / 't.niko').write_text(src, encoding='utf8')
        r = _niko2('run', 't.niko', cwd=tmp)
        assert r.returncode == 0, f'vm[{name}] failed:\n{r.stdout}{r.stderr}'
        assert r.stdout == want, \
            f'vm[{name}] mismatch:\n{r.stdout!r} != {want!r}'
        vm_out = r.stdout
        if node:
            r = _niko2('wasm', 't.niko', '--run', cwd=tmp)
            assert r.returncode == 0, \
                f'wasm[{name}] failed:\n{r.stdout}{r.stderr}'
            wasm_out = _strip_banner(r.stdout)
            assert wasm_out == vm_out, \
                f'3-way[{name}] WASM != VM:\n{wasm_out!r}\n{vm_out!r}'
        else:
            print('SKIP 3-way WASM: node.js not on PATH')
        if cc:
            r = _niko2('native', 't.niko', '--run', cwd=tmp)
            assert r.returncode == 0, \
                f'native[{name}] failed:\n{r.stdout}{r.stderr}'
            native_out = _strip_banner(r.stdout)
            assert native_out == vm_out, \
                f'3-way[{name}] native != VM:\n{native_out!r}\n{vm_out!r}'
        else:
            print('SKIP 3-way native: no C compiler on PATH')

# --- out-of-range still fails loudly on every backend ---------------------
with tempfile.TemporaryDirectory() as t:
    tmp = pathlib.Path(t)
    (tmp / 'oob.niko').write_text('say item_of(9, "héllo")\n', encoding='utf8')
    r = _niko2('run', 'oob.niko', cwd=tmp)
    assert r.returncode != 0, 'vm[oob] should have failed'
    assert 'string index out of range' in r.stdout + r.stderr
    legs = [('wasm', node, ('wasm', 'oob.niko', '--run')),
            ('native', cc, ('native', 'oob.niko', '--run'))]
    for leg, have_tool, args in legs:
        if not have_tool:
            print(f'SKIP oob {leg}: toolchain not on PATH')
            continue
        r = _niko2(*args, cwd=tmp)
        combined = _strip_banner(r.stdout) + r.stderr
        assert r.returncode != 0, f'{leg}[oob] should have failed'
        assert 'string index out of range' in combined, \
            f'{leg}[oob] wrong error:\n{combined!r}'

print('ALL WASM UNICODE TESTS PASSED')
