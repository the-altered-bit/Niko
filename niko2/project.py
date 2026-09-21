"""Niko 2 project and package metadata helpers (Alpha 4/6)."""
from pathlib import Path
import json

DEFAULT_MANIFEST = """name = \"niko_app\"\nversion = \"0.1.0\"\nentry = \"main.niko\"\n\n[dependencies]\n"""


def find_project_root(start):
    p = Path(start).resolve()
    if p.is_file(): p = p.parent
    for cur in [p, *p.parents]:
        if (cur / 'niko.toml').is_file(): return cur
    return p


def read_manifest(root):
    path = Path(root) / 'niko.toml'
    if not path.exists(): return {'name':'niko_app','version':'0.1.0','entry':'main.niko','dependencies':{}}
    try:
        import tomllib
        with path.open('rb') as f: data=tomllib.load(f)
        data.setdefault('dependencies', {})
        return data
    except Exception as e:
        raise ValueError(f'cannot read niko.toml: {e}')


def init_project(path):
    root=Path(path)
    root.mkdir(parents=True, exist_ok=True)
    manifest=root/'niko.toml'
    main=root/'main.niko'
    if not manifest.exists(): manifest.write_text(DEFAULT_MANIFEST, encoding='utf8')
    if not main.exists(): main.write_text('say "Hello from Niko"\n', encoding='utf8')
    return root


def _strip_quotes(value):
    return value.strip().strip('"\'')


def _iter_niko_files(root):
    root = Path(root)
    for path in sorted(root.rglob('*.niko')):
        if path.name.startswith('.'):
            continue
        yield path


def _module_dependencies_for_file(path):
    from .parser import parse
    try:
        tree = parse(path.read_text(encoding='utf8'))
    except Exception:
        return []
    deps = []
    for node in getattr(tree, 'body', []):
        if node.__class__.__name__ == 'UseStmt':
            mod = _strip_quotes(node.module)
            if mod in {'math','text','collections','time'}:
                continue
            deps.append(mod)
    return sorted(set(deps))


def collect_project_dependencies(root):
    root = Path(root)
    graph = {}
    for path in _iter_niko_files(root):
        rel = path.relative_to(root).as_posix()
        graph[rel] = _module_dependencies_for_file(path)
    return graph


def write_lock_file(root, graph=None):
    root = Path(root)
    if graph is None:
        graph = collect_project_dependencies(root)
    payload = {'version': 1, 'dependencies': graph}
    lock_path = root / 'niko.lock'
    lock_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf8')
    return lock_path


def read_lock_file(root):
    lock_path = Path(root) / 'niko.lock'
    if not lock_path.exists():
        return {'version': 1, 'dependencies': {}}
    try:
        return json.loads(lock_path.read_text(encoding='utf8'))
    except json.JSONDecodeError as exc:
        raise ValueError(f'cannot read niko.lock: {exc}')
