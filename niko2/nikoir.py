"""Serialization for compiled Niko IR (.nikoir files).

Alpha 5 milestone: a .nikoir file produced by `niko2 build` can be loaded
back and executed directly by the VM, without re-parsing or re-typechecking
the original .niko source. This is the first step toward treating .nikoir
as a real distributable artifact (and, eventually, an input format for
native/WASM backends).
"""
import json
from pathlib import Path
from .ir import Instruction, FunctionCode, ModuleCode

NIKOIR_FORMAT_VERSION = '2'  # bump if the on-disk shape changes


class NikoIRError(Exception):
    pass


def _code_to_json(code):
    return [{'op': ins.op, 'arg': ins.arg, 'line': ins.line} for ins in code]


def _code_from_json(items):
    return [Instruction(x['op'], x.get('arg'), x.get('line', 0)) for x in items]


def module_to_dict(module, engine_version='2.0.0-alpha.5'):
    return {
        'nikoir_format': NIKOIR_FORMAT_VERSION,
        'engine_version': engine_version,
        'constants': module.constants,
        'code': _code_to_json(module.code),
        'functions': {
            name: {
                'name': fc.name,
                'params': fc.params,
                'return_type': fc.return_type,
                'constants': fc.constants or [],
                'code': _code_to_json(fc.code),
                'qualname': fc.qualname,
                'captures': list(fc.captures),
                'nested': fc.nested,
            }
            for name, fc in module.functions.items()
        },
    }


def save_nikoir(module, path, engine_version='2.0.0-alpha.5'):
    payload = module_to_dict(module, engine_version)
    Path(path).write_text(json.dumps(payload, indent=2, default=str), encoding='utf8')
    return path


def module_from_dict(data):
    fmt = data.get('nikoir_format')
    if fmt is None:
        # Alpha 4 build output had no nikoir_format tag. Accept it as format "0"
        # so older artifacts still load; only refuse formats newer than we know.
        fmt = '0'
    if fmt not in ('0', '1', NIKOIR_FORMAT_VERSION):
        raise NikoIRError(
            f'This .nikoir file uses format {fmt!r}, which this build of niko2 '
            f'does not understand (it knows format {NIKOIR_FORMAT_VERSION!r}).'
        )
    try:
        functions = {}
        for name, fdata in data.get('functions', {}).items():
            functions[name] = FunctionCode(
                name=fdata.get('name', name),
                params=fdata['params'],
                code=_code_from_json(fdata['code']),
                return_type=fdata.get('return_type'),
                constants=fdata.get('constants') or [],
                qualname=fdata.get('qualname', name),
                captures=tuple(fdata.get('captures') or ()),
                nested=bool(fdata.get('nested', False)),
            )
        return ModuleCode(
            code=_code_from_json(data['code']),
            functions=functions,
            constants=data.get('constants', []),
        )
    except (KeyError, TypeError) as e:
        raise NikoIRError(f'Malformed .nikoir file: missing {e}')


def load_nikoir(path):
    try:
        text = Path(path).read_text(encoding='utf8')
    except OSError as e:
        raise NikoIRError(str(e))
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise NikoIRError(f'{path} is not a valid .nikoir (JSON) file: {e}')
    return module_from_dict(data)
