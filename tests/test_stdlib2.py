#!/usr/bin/env python3
"""Alpha 14: Niko 2 standard library.

- Every `niko2/stdlib/*.niko` file must pass `niko2 check`.
- Every documented `example:` is EXECUTED: the doc examples are the test
  corpus. After `->` comes exactly what `say <code>` prints.
- The same example programs run 3-way differential (VM/WASM/native):
  byte-identical stdout. WASM/native legs skip cleanly without node/cc.
- `STDLIB.md` is generated from the doc comments by render_stdlib_docs();
  the suite fails if it is stale. Regenerate with:
      python3 tests/test_stdlib2.py --write-docs
- Error cases: missing stdlib module, bad suffix, NIKO_PATH shadowing.

Usage: python3 tests/test_stdlib2.py [--write-docs]
"""
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap

root = pathlib.Path(__file__).resolve().parent
project_root = root.parent
sys.path.insert(0, str(project_root))

from niko2.modules import stdlib_dir  # noqa: E402

STDLIB_DIR = stdlib_dir()
MODULES = ['text', 'math', 'lists', 'records', 'json']


# ---------------------------------------------------------------------------
# Doc-comment parsing
# ---------------------------------------------------------------------------

def _strip_hash(line):
    if not line.startswith('#'):
        return None
    rest = line[1:]
    if rest.startswith(' '):
        rest = rest[1:]
    return rest


def _parse_doc_block(block, to_name):
    """block: doc comment lines (without '#') preceding a `to` line."""
    assert block, f'missing doc comment for {to_name}'
    sig = block[0].strip()
    sig_name = sig.split('(')[0].strip()
    assert sig_name == to_name, \
        f'doc signature {sig_name!r} does not match function {to_name!r}'
    desc, examples = [], []
    j = 1
    while j < len(block):
        s = block[j].strip()
        if s == 'example:':
            j += 1
            raw = []
            while j < len(block) and block[j].startswith('  '):
                raw.append(block[j])
                j += 1
            assert raw, f'empty example block for {to_name}'
            dedented = textwrap.dedent('\n'.join(raw)).splitlines()
            assert ' -> ' in dedented[-1], \
                f'example for {to_name} needs `code -> expected` on its last line'
            code, expected = dedented[-1].rsplit(' -> ', 1)
            examples.append((dedented[:-1] + [code.strip()],
                             expected.strip()))
        elif s.startswith('example:'):
            rest = s[len('example:'):].strip()
            assert ' -> ' in rest, f'example for {to_name} needs `code -> expected`'
            code, expected = rest.rsplit(' -> ', 1)
            examples.append(([code.strip()], expected.strip()))
            j += 1
        else:
            desc.append(block[j].strip())
            j += 1
    assert examples, f'no example documented for {to_name}'
    return sig, desc, examples


def parse_module(path):
    """Return (header_lines, [(sig, desc, examples)])."""
    lines = path.read_text(encoding='utf8').splitlines()
    i, n = 0, len(lines)
    header = []
    while i < n:
        c = _strip_hash(lines[i])
        if c is None:
            break
        header.append(c)
        i += 1
    funcs = []
    while i < n:
        line = lines[i]
        if line.strip() == '' or not line.startswith('#'):
            i += 1
            continue
        block = []
        while i < n and lines[i].startswith('#'):
            block.append(_strip_hash(lines[i]))
            i += 1
        while i < n and lines[i].strip() == '':
            i += 1
        m = re.match(r'^to\s+([A-Za-z_][A-Za-z0-9_]*)\b', lines[i] if i < n else '')
        looks_like_sig = re.match(r'^[A-Za-z_][A-Za-z0-9_]*\s*\(.*\)\s*->',
                                  block[0].strip())
        if m and looks_like_sig:
            funcs.append(_parse_doc_block(block, m.group(1)))
            i += 1
        # else: stray comment block (divider, or inside a body) -- ignore
    return header, funcs


def _render_header_rest(rest):
    out, para, code = [], [], []
    def flush():
        if para:
            out.append('\n'.join(para) + '\n')
            para.clear()
        if code:
            out.append('```niko\n' + '\n'.join(code) + '\n```\n')
            code.clear()
    for line in rest:
        if line.strip() == '':
            flush()
        elif line.startswith('  '):
            if para:
                flush()
            code.append(line.strip())
        else:
            if code:
                flush()
            para.append(line.strip())
    flush()
    return out


def render_stdlib_docs():
    out = []
    out.append('# Niko 2 Standard Library\n')
    out.append('Pure-Niko modules bundled with Niko 2. They behave identically on')
    out.append('the VM, WebAssembly, and native backends. Import one with:\n')
    out.append('```niko')
    out.append('import "stdlib/text.niko" as text')
    out.append('say text.title("hello world")')
    out.append('```\n')
    out.append('`import` searches the importing file\u2019s directory first, then each')
    out.append('`NIKO_PATH` directory, then the bundled standard library (for paths')
    out.append('starting with `stdlib/`), then the current working directory. A `stdlib/`')
    out.append('folder next to your file or on `NIKO_PATH` shadows the bundled one.\n')
    for mod in MODULES:
        header, funcs = parse_module(STDLIB_DIR / f'{mod}.niko')
        m = re.match(r'stdlib/(\w+)\s*--\s*(.*)', header[0]) if header else None
        blurb = m.group(2).strip() if m else ''
        out.append(f'## stdlib/{mod} \u2014 {blurb}\n')
        out.extend(_render_header_rest(header[1:]))
        for sig, desc, examples in funcs:
            out.append(f'### `{sig}`\n')
            if desc:
                out.append('\n'.join(d for d in desc if d) + '\n')
            for code_lines, expected in examples:
                out.append('```niko')
                out.extend(code_lines)
                out.append('```')
                out.append(f'Output: `{expected}`\n')
    return '\n'.join(out).rstrip() + '\n'


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------

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


IMPORT_ALL = '\n'.join(f'import "stdlib/{m}.niko" as {m}' for m in MODULES)


def _example_program(mod, code_lines):
    return IMPORT_ALL + '\n' + '\n'.join(code_lines) + '\n'


node = shutil.which('node')
cc = shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')


def _run_backends(program_path, cwd):
    """Returns (vm_out, wasm_out|None, native_out|None)."""
    r = _niko2('run', str(program_path), cwd=cwd)
    assert r.returncode == 0, f'vm failed:\n{r.stdout}{r.stderr}'
    vm_out = r.stdout
    wasm_out, native_out = None, None
    if node:
        r = _niko2('wasm', str(program_path), '--run', cwd=cwd)
        assert r.returncode == 0, f'wasm failed:\n{r.stdout}{r.stderr}'
        wasm_out = _strip_banner(r.stdout)
    if cc:
        r = _niko2('native', str(program_path), '--run', cwd=cwd)
        assert r.returncode == 0, f'native failed:\n{r.stdout}{r.stderr}'
        native_out = _strip_banner(r.stdout)
    return vm_out, wasm_out, native_out


if __name__ == '__main__' and '--write-docs' in sys.argv:
    (project_root / 'STDLIB.md').write_text(render_stdlib_docs(), encoding='utf8')
    print('wrote STDLIB.md')
    sys.exit(0)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

# 1. every stdlib file typechecks, has full doc coverage, and a clean top level
for mod in MODULES:
    p = STDLIB_DIR / f'{mod}.niko'
    assert p.is_file(), f'missing {p}'
    r = _niko2('check', str(p), cwd=str(project_root))
    assert r.returncode == 0 and 'no type errors' in r.stdout, \
        f'stdlib/{mod}.niko failed check:\n{r.stdout}{r.stderr}'
    header, funcs = parse_module(p)
    assert header and re.match(r'stdlib/\w+\s*--', header[0]), \
        f'stdlib/{mod}.niko needs a `# stdlib/{mod} -- blurb` header'
    src = p.read_text(encoding='utf8')
    to_names = re.findall(r'^to\s+([A-Za-z_][A-Za-z0-9_]*)\b', src, re.M)
    doc_names = [sig.split('(')[0] for sig, _, _ in funcs]
    assert sorted(to_names) == sorted(doc_names), \
        f'stdlib/{mod}.niko: functions {to_names} != documented {doc_names}'
    assert not re.search(r'^say ', src, re.M), \
        f'stdlib/{mod}.niko: no top-level say allowed in a library'
    assert not re.search(r'^set ', src, re.M), \
        f'stdlib/{mod}.niko: no top-level set allowed (keep the namespace clean)'
    print(f'ok: stdlib/{mod}.niko checks, documented, clean')

# 2. every doc example executes and prints exactly the documented output (VM),
#    and the whole module corpus is byte-identical on VM/WASM/native.
for mod in MODULES:
    _, funcs = parse_module(STDLIB_DIR / f'{mod}.niko')
    module_prog = []
    with tempfile.TemporaryDirectory() as t:
        for sig, _desc, examples in funcs:
            name = sig.split('(')[0]
            for code_lines, expected in examples:
                prog = _example_program(mod, code_lines[:-1] + [f'say {code_lines[-1]}'])
                prog_file = pathlib.Path(t) / 'ex.niko'
                prog_file.write_text(prog, encoding='utf8')
                r = _niko2('run', 'ex.niko', cwd=t)
                assert r.returncode == 0, \
                    f'example {mod}.{name} failed:\n{prog}\n{r.stdout}{r.stderr}'
                got = r.stdout.strip('\n')
                assert got == expected, \
                    f'example {mod}.{name} lied:\n  code: {code_lines[-1]}\n' \
                    f'  documented: {expected!r}\n  actual:     {got!r}'
                module_prog.extend(code_lines[:-1] + [f'say {code_lines[-1]}'])
        # one program per module for the 3-way differential + determinism
        prog_file = pathlib.Path(t) / 'all.niko'
        prog_file.write_text(_example_program(mod, module_prog), encoding='utf8')
        vm_out, wasm_out, native_out = _run_backends(prog_file, t)
        if wasm_out is not None:
            assert wasm_out == vm_out, f'3-way[{mod}] WASM != VM:\n{wasm_out!r}\n{vm_out!r}'
        else:
            print('SKIP 3-way WASM: node.js not on PATH')
        if native_out is not None:
            assert native_out == vm_out, \
                f'3-way[{mod}] native != VM:\n{native_out!r}\n{vm_out!r}'
        else:
            print('SKIP 3-way native: no C compiler on PATH')
        vm_out2, _, _ = _run_backends(prog_file, t)
        assert vm_out2 == vm_out, f'determinism[{mod}]: second run differed'
    print(f'ok: stdlib/{mod} examples green, 3-way identical, deterministic')

# 3. error cases
with tempfile.TemporaryDirectory() as t:
    (pathlib.Path(t) / 'bad.niko').write_text(
        'import "stdlib/nope.niko" as n\n', encoding='utf8')
    r = _niko2('run', 'bad.niko', cwd=t)
    assert r.returncode != 0 and 'cannot find module "stdlib/nope.niko"' in r.stdout, \
        f'missing stdlib module:\n{r.stdout}{r.stderr}'
    print('ok: error/missing-stdlib-module')

    (pathlib.Path(t) / 'bad2.niko').write_text(
        'import "stdlib/text.txt" as n\n', encoding='utf8')
    r = _niko2('run', 'bad2.niko', cwd=t)
    assert r.returncode != 0 and 'import expects a .niko file' in r.stdout, \
        f'bad suffix:\n{r.stdout}{r.stderr}'
    print('ok: error/bad-suffix')

# 4. NIKO_PATH shadows the bundled stdlib
with tempfile.TemporaryDirectory() as t:
    over = pathlib.Path(t) / 'over' / 'stdlib'
    over.mkdir(parents=True)
    (over / 'text.niko').write_text(
        'to shout with s:\n    give back "SHADOWED"\n', encoding='utf8')
    (pathlib.Path(t) / 'app.niko').write_text(
        'import "stdlib/text.niko" as text\nsay text.shout("hi")\n', encoding='utf8')
    r = _niko2('run', 'app.niko', cwd=t, env={'NIKO_PATH': str(pathlib.Path(t) / 'over')})
    assert r.returncode == 0 and r.stdout.strip() == 'SHADOWED', \
        f'NIKO_PATH shadow:\n{r.stdout}{r.stderr}'
    print('ok: NIKO_PATH shadows bundled stdlib')

# 5. STDLIB.md is fresh
fresh = render_stdlib_docs()
on_disk = (project_root / 'STDLIB.md').read_text(encoding='utf8') \
    if (project_root / 'STDLIB.md').exists() else None
assert on_disk == fresh, \
    'STDLIB.md is stale -- regenerate with: python3 tests/test_stdlib2.py --write-docs'
print('ok: STDLIB.md fresh')

print('ALL STDLIB2 TESTS PASSED')
