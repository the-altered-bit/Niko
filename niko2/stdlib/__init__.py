"""Niko standard-library registry. Alpha 5 exposes a small module API."""
from ..runtime import get_builtin

CORE_MODULES = {
    'math': {'abs','ceil','floor','round','sqrt','pi','max','min','sum','average','random_int'},
    'text': {'text','number','upper','lower','trim','replace','split','join','count_of','starts_with','ends_with'},
    'collections': {'sorted','reversed','unique','pick','has','keys','length','item_of'},
    'time': {'today','now','sleep'},
    'files': {'write_file','append_file','read_file','read_lines','file_exists'},
    'result': {'ok','error','is_ok','is_error','unwrap','unwrap_or','error_message',
               'try_read_file','try_number'},
}


def module_names(name):
    return CORE_MODULES.get(name, set())


def module_symbols(name):
    names = module_names(name)
    return {sym: get_builtin(sym) for sym in names if get_builtin(sym) is not None}
