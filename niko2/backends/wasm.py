"""Niko WebAssembly backend (Alpha 8).

Compiles a checked Niko AST directly to a WebAssembly binary. See
``niko2/backends/WASM_DESIGN.md`` for the value model and memory layout.

No third-party libraries are used: the WASM binary is emitted by hand.
Run the result with the bundled Node.js host shim::

    niko2 wasm program.niko --run
"""
import math
import struct

from ..ast import (
    Program, SetStmt, IndexSetStmt, AugAssignStmt, PutStmt, RemoveStmt,
    AskStmt, SayStmt, AssertStmt, ExprStmt, IfStmt, RepeatStmt, ForStmt, WhileStmt,
    StopStmt, SkipStmt, ReturnStmt, UseStmt, MatchStmt, MatchExpr, FunctionDef,
    CallExpr, NameExpr, LiteralExpr, ListExpr, RecordExpr, IndexExpr,
    UnaryExpr, BinaryExpr, AttrExpr, MatchLit, MatchBind, MatchOk, MatchErr,
    MatchList, MatchRest, MatchRecord,
)
from ..compiler import CompileError
from ..typecheck import BUILTIN_NAMES
from ..closures import analyze_closures
from . import Backend

I32 = 0x7F
I64 = 0x7E
F32 = 0x7D
F64 = 0x7C

# Value tags (see WASM_DESIGN.md).
TAG_NOTHING = 0
TAG_NUMBER = 1
TAG_TEXT = 2
TAG_YESNO = 3
TAG_LIST = 4
TAG_RECORD = 5
TAG_RESULT = 6
TAG_FUNCTION = 7  # Alpha 10: first-class function value

SCRATCH = 0x0000          # host scratch region (4 KiB)
SCRATCH_SIZE = 0x1000
DATA_BASE = 0x1000        # static data starts here


def uleb(n):
    if n < 0:
        raise ValueError("uleb of negative")
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def sleb(n):
    out = bytearray()
    more = True
    while more:
        b = n & 0x7F
        n >>= 7
        if (n == 0 and b & 0x40 == 0) or (n == -1 and b & 0x40):
            more = False
        else:
            b |= 0x80
        out.append(b)
    return bytes(out)


def vec(items):
    return uleb(len(items)) + b"".join(items)


def enc_str(s):
    b = s.encode("utf-8")
    return uleb(len(b)) + b


class W:
    """Instruction writer for one function body, with label tracking."""

    def __init__(self):
        self.b = bytearray()
        self.labels = []
        self._next = 0

    # -- structured control -------------------------------------------------
    def _lbl(self):
        i = self._next
        self._next += 1
        self.labels.append(i)
        return i

    def _depth(self, lbl):
        return len(self.labels) - 1 - self.labels.index(lbl)

    def block(self):
        i = self._lbl()
        self.b += b"\x02\x40"
        return i

    def loop(self):
        i = self._lbl()
        self.b += b"\x03\x40"
        return i

    def if_(self):
        i = self._lbl()
        self.b += b"\x04\x40"
        return i

    def else_(self):
        self.b += b"\x05"

    def end(self):
        self.b += b"\x0b"
        self.labels.pop()

    def br(self, lbl):
        self.b += b"\x0c" + uleb(self._depth(lbl))

    def br_if(self, lbl):
        self.b += b"\x0d" + uleb(self._depth(lbl))

    def return_(self):
        self.b += b"\x0f"

    def unreachable(self):
        self.b += b"\x00"

    def call(self, idx):
        self.b += b"\x10" + uleb(idx)

    def call_indirect(self, typeidx, tableidx=0):
        # Alpha 10: uniform indirect calls of Niko functions through the
        # single funcref table. Stack: [args..., table_index].
        self.b += b"\x11" + uleb(typeidx) + uleb(tableidx)

    # -- parametric ---------------------------------------------------------
    def drop(self):
        self.b += b"\x1a"

    # -- variables ----------------------------------------------------------
    def local_get(self, i):
        self.b += b"\x20" + uleb(i)

    def local_set(self, i):
        self.b += b"\x21" + uleb(i)

    def local_tee(self, i):
        self.b += b"\x22" + uleb(i)

    def global_get(self, i):
        self.b += b"\x23" + uleb(i)

    def global_set(self, i):
        self.b += b"\x24" + uleb(i)

    # -- memory -------------------------------------------------------------
    def _memarg(self, align, offset):
        return uleb(align) + uleb(offset)

    def i32_load(self, offset=0):
        self.b += b"\x28" + self._memarg(2, offset)

    def f64_load(self, offset=0):
        self.b += b"\x2b" + self._memarg(3, offset)

    def i32_load8_u(self, offset=0):
        self.b += b"\x2d" + self._memarg(0, offset)

    def i32_store(self, offset=0):
        self.b += b"\x36" + self._memarg(2, offset)

    def f64_store(self, offset=0):
        self.b += b"\x39" + self._memarg(3, offset)

    def i32_store8(self, offset=0):
        self.b += b"\x3a" + self._memarg(0, offset)

    def memory_copy(self):
        self.b += b"\xfc\x0a\x00\x00"

    def memory_size(self):
        self.b += b"\x3f\x00"

    def memory_grow(self):
        self.b += b"\x40\x00"

    def select(self):
        self.b += b"\x1b"

    # -- consts -------------------------------------------------------------
    def i32_const(self, n):
        self.b += b"\x41" + sleb(n)

    def f64_const(self, f):
        self.b += b"\x44" + struct.pack("<d", f)

    # -- i32 ops ------------------------------------------------------------
    def i32_eqz(self):
        self.b += b"\x45"

    def i32_eq(self):
        self.b += b"\x46"

    def i32_ne(self):
        self.b += b"\x47"

    def i32_lt_s(self):
        self.b += b"\x48"

    def i32_lt_u(self):
        self.b += b"\x49"

    def i32_gt_s(self):
        self.b += b"\x4a"

    def i32_gt_u(self):
        self.b += b"\x4b"

    def i32_le_s(self):
        self.b += b"\x4c"

    def i32_le_u(self):
        self.b += b"\x4d"

    def i32_ge_s(self):
        self.b += b"\x4e"

    def i32_ge_u(self):
        self.b += b"\x4f"

    def i32_add(self):
        self.b += b"\x6a"

    def i32_sub(self):
        self.b += b"\x6b"

    def i32_mul(self):
        self.b += b"\x6c"

    def i32_div_s(self):
        self.b += b"\x6d"

    def i32_and(self):
        self.b += b"\x71"

    def i32_or(self):
        self.b += b"\x72"

    def i32_xor(self):
        self.b += b"\x73"

    def i32_shl(self):
        self.b += b"\x74"

    def i32_shr_u(self):
        self.b += b"\x76"

    def i32_trunc_f64_s(self):
        self.b += b"\xaa"

    # -- f64 ops ------------------------------------------------------------
    def f64_eq(self):
        self.b += b"\x61"

    def f64_ne(self):
        self.b += b"\x62"

    def f64_lt(self):
        self.b += b"\x63"

    def f64_gt(self):
        self.b += b"\x64"

    def f64_le(self):
        self.b += b"\x65"

    def f64_ge(self):
        self.b += b"\x66"

    def f64_add(self):
        self.b += b"\xa0"

    def f64_sub(self):
        self.b += b"\xa1"

    def f64_mul(self):
        self.b += b"\xa2"

    def f64_div(self):
        self.b += b"\xa3"

    def f64_neg(self):
        self.b += b"\x9a"

    def f64_abs(self):
        self.b += b"\x99"

    def f64_ceil(self):
        self.b += b"\x9b"

    def f64_floor(self):
        self.b += b"\x9c"

    def f64_trunc(self):
        self.b += b"\x9d"

    def f64_nearest(self):
        self.b += b"\x9e"

    def f64_sqrt(self):
        self.b += b"\x9f"

    def f64_convert_i32_s(self):
        self.b += b"\xb7"

    def bytes(self):
        return bytes(self.b)


class Func:
    def __init__(self, idx, params, results):
        self.idx = idx
        self.params = params
        self.results = results
        self.w = W()
        self.locals = list(params)  # params come first

    def new_local(self, vtype=I32):
        self.locals.append(vtype)
        return len(self.locals) - 1


class WasmModule:
    def __init__(self):
        self.types = []
        self.type_map = {}
        self.imports = []          # (module, name, typeidx)
        self.funcs = []            # Func, in index order after imports
        self.globals = []          # (vtype, mutable, init_bytes)
        self.exports = []          # (name, kind, idx)
        self.mem_min = 64
        self.datas = []            # (offset, bytes)
        # Alpha 10: one funcref table holding every user (Niko) function, in
        # declaration order. Function values store the table index.
        self.table = []            # funcidx per table slot

    def add_type(self, params, results):
        key = (tuple(params), tuple(results))
        if key in self.type_map:
            return self.type_map[key]
        self.types.append(key)
        idx = len(self.types) - 1
        self.type_map[key] = idx
        return idx

    def add_import(self, module, name, params, results):
        t = self.add_type(params, results)
        self.imports.append((module, name, t))
        return len(self.imports) - 1

    def add_function(self, params, results):
        t = self.add_type(params, results)
        f = Func(len(self.imports) + len(self.funcs), params, results)
        f._typeidx = t
        self.funcs.append(f)
        return f

    def add_global(self, vtype=I32, mutable=True, init=0):
        init_b = b"\x41" + sleb(init) + b"\x0b"
        self.globals.append((vtype, mutable, init_b))
        return len(self.globals) - 1

    def add_table_entry(self, funcidx):
        """Register a user function in the funcref table; returns its index."""
        self.table.append(funcidx)
        return len(self.table) - 1

    def add_export(self, name, kind, idx):
        self.exports.append((name, kind, idx))

    def add_data(self, offset, data):
        self.datas.append((offset, bytes(data)))

    def emit(self):
        out = bytearray(b"\x00asm\x01\x00\x00\x00")

        def section(num, payload):
            out.extend(uleb(num))
            out.extend(uleb(len(payload)))
            out.extend(payload)

        if self.types:
            p = vec([b"\x60" + vec([bytes([t]) for t in ps])
                     + vec([bytes([t]) for t in rs])
                     for ps, rs in self.types])
            section(1, p)
        if self.imports:
            p = vec([enc_str(m) + enc_str(n) + b"\x00" + uleb(t)
                     for m, n, t in self.imports])
            section(2, p)
        if self.funcs:
            section(3, vec([uleb(f._typeidx) for f in self.funcs]))
        # table section (4): one funcref table for the user functions. Always
        # emitted (possibly empty): call_fn's call_indirect is validated
        # statically even in programs that define no functions.
        section(4, vec([b"\x70\x00" + uleb(len(self.table))]))
        # memory
        section(5, vec([b"\x00" + uleb(self.mem_min)]))
        if self.globals:
            p = vec([bytes([vt]) + bytes([0x01 if mut else 0x00]) + init
                     for vt, mut, init in self.globals])
            section(6, p)
        if self.exports:
            kinds = {"func": 0, "memory": 2, "global": 3}
            p = vec([enc_str(n) + bytes([kinds[k]]) + uleb(i)
                     for n, k, i in self.exports])
            section(7, p)
        if self.table:
            # elem section (9): single active segment filling the table from 0
            section(9, vec([b"\x00" + b"\x41\x00\x0b"
                            + vec([uleb(f) for f in self.table])]))
        if self.funcs:
            bodies = []
            for f in self.funcs:
                # locals vec: runs of (count, valtype), skipping params
                extra = f.locals[len(f.params):]
                runs = []
                for vt in extra:
                    if runs and runs[-1][1] == vt:
                        runs[-1][0] += 1
                    else:
                        runs.append([1, vt])
                body = vec([uleb(c) + bytes([vt]) for c, vt in runs])
                body += f.w.bytes() + b"\x0b"
                bodies.append(uleb(len(body)) + body)
            section(10, vec(bodies))
        if self.datas:
            p = vec([b"\x00" + b"\x41" + sleb(off) + b"\x0b" + uleb(len(d)) + d
                     for off, d in self.datas])
            section(11, p)
        return bytes(out)


# ---------------------------------------------------------------------------
# Compiler
# ---------------------------------------------------------------------------

class WasmCompileError(Exception):
    """Raised for programs the WASM backend cannot compile yet."""

    def __init__(self, message, line=0):
        super().__init__(message)
        self.message = message
        self.line = line


class WasmCompiler:
    def __init__(self):
        self.m = WasmModule()
        self.data_ptr = DATA_BASE
        self.lit_cache = {}       # bytes -> addr
        self.text_cache = {}      # str -> value struct addr
        self.imp = {}             # name -> funcidx
        self.h = {}               # helper name -> funcidx
        self.user_funcs = {}      # niko name -> (funcidx, [param names])
        self.glob = {}            # niko name -> globalidx
        self.heap_g = None
        self.match_g = None
        self.nothing_addr = self.yes_addr = self.no_addr = None
        self.yes_text = self.no_text = self.nothing_text = None
        self.func_stack = []      # active _Frame list (for closure detection)

    # -- static data --------------------------------------------------------
    def lit_bytes(self, b):
        if b in self.lit_cache:
            return self.lit_cache[b]
        addr = self.data_ptr
        self.m.add_data(addr, b)
        self.data_ptr += len(b)
        self.lit_cache[b] = addr
        return addr

    def text_val(self, s):
        """Address of a static boxed text value for the Python string s."""
        if s in self.text_cache:
            return self.text_cache[s]
        b = s.encode("utf-8")
        baddr = self.lit_bytes(b)
        vaddr = self.data_ptr
        self.m.add_data(vaddr, struct.pack("<i", TAG_TEXT)
                        + struct.pack("<i", baddr) + struct.pack("<i", len(b)))
        self.data_ptr += 12
        self.text_cache[s] = vaddr
        return vaddr

    def static_val(self, tag, payload_bytes):
        vaddr = self.data_ptr
        self.m.add_data(vaddr, struct.pack("<i", tag) + payload_bytes)
        self.data_ptr += 4 + len(payload_bytes)
        return vaddr

    def emit_panic(self, w, msg):
        b = msg.encode("utf-8")
        addr = self.lit_bytes(b)
        w.i32_const(addr)
        w.i32_const(len(b))
        w.call(self.imp["panic"])
        w.unreachable()

    # -- setup --------------------------------------------------------------
    def _setup(self):
        m = self.m
        self.imp["panic"] = m.add_import("niko", "niko_panic", [I32, I32], [])
        self.imp["print"] = m.add_import("niko", "niko_print", [I32, I32], [])
        self.imp["fmt_num"] = m.add_import("niko", "niko_fmt_num", [F64], [I32])
        self.imp["input"] = m.add_import("niko", "niko_input", [I32, I32], [I32, I32])
        self.imp["parse_num"] = m.add_import("niko", "niko_parse_num", [I32, I32], [F64])
        self.imp["random_i32"] = m.add_import("niko", "niko_random_i32", [I32, I32], [I32])
        self.imp["today"] = m.add_import("niko", "niko_today", [], [I32, I32])
        self.imp["now"] = m.add_import("niko", "niko_now", [], [I32, I32])
        self.imp["sleep"] = m.add_import("niko", "niko_sleep", [F64], [])
        self.imp["pow"] = m.add_import("niko", "niko_pow", [F64, F64], [F64])
        self.heap_g = m.add_global(I32, True, 0)   # patched after data layout
        self.match_g = m.add_global(I32, True, 0)
        self.nothing_addr = self.static_val(TAG_NOTHING, b"")
        self.yes_addr = self.static_val(TAG_YESNO, struct.pack("<i", 1))
        self.no_addr = self.static_val(TAG_YESNO, struct.pack("<i", 0))
        self.yes_text = self.text_val("yes")
        self.no_text = self.text_val("no")
        self.nothing_text = self.text_val("nothing")
        # Alpha 10: the single WASM type of every Niko function:
        # (env_ptr, nargs, args_ptr) -> boxed value pointer.
        self.niko_fn_type = m.add_type([I32, I32, I32], [I32])
        self._build_helpers()
        # $heap starts after all static data (helpers add their literals).
        m.globals[self.heap_g] = (I32, True, b"\x41" + sleb(self.data_ptr) + b"\x0b")

    def _helper(self, name, params, results):
        # Idempotent: declares the signature on first call, returns the
        # existing Func afterwards. All signatures are declared up front
        # (see _build_helpers) so helper bodies can call each other freely.
        if name in self.hf:
            return self.hf[name]
        f = self.m.add_function(params, results)
        self.hf[name] = f
        self.h[name] = f.idx
        return f

    # (name, params, results) for every runtime helper, in index order.
    HELPER_SIGS = [
        ("alloc", [I32], [I32]),
        ("make_number", [F64], [I32]),
        ("make_text", [I32, I32], [I32]),
        ("text_concat", [I32, I32], [I32]),
        ("concat3", [I32, I32, I32], [I32]),
        ("text_eq", [I32, I32], [I32]),
        ("text_cmp", [I32, I32], [I32]),
        ("text_find", [I32, I32], [I32]),
        ("utf8_len", [I32, I32], [I32]),
        ("utf8_byte_offset", [I32, I32, I32], [I32]),
        ("sb_new", [], [I32]),
        ("sb_push", [I32, I32, I32], []),
        ("sb_push_text", [I32, I32], []),
        ("sb_push_lit", [I32, I32, I32], []),
        ("sb_finish", [I32], [I32]),
        ("to_text", [I32], [I32]),
        ("list_text", [I32, I32], [I32]),
        ("fmt_nested", [I32], [I32]),
        ("record_text", [I32, I32], [I32]),
        ("py_str", [I32, I32], [I32]),
        ("truthy", [I32], [I32]),
        ("equals", [I32, I32], [I32]),
        ("list_eq", [I32, I32], [I32]),
        ("record_eq", [I32, I32], [I32]),
        ("panic_line", [I32, I32, I32], []),
        ("to_int", [I32, I32], [I32]),
        ("in_op", [I32, I32, I32], [I32]),
        ("binary", [I32, I32, I32, I32], [I32]),
        ("unary", [I32, I32, I32], [I32]),
        ("list_new", [], [I32]),
        ("list_push", [I32, I32], [I32]),
        ("list_get0", [I32, I32], [I32]),
        ("list_len", [I32], [I32]),
        ("index_get", [I32, I32, I32], [I32]),
        ("index_set", [I32, I32, I32, I32], []),
        ("record_new", [], [I32]),
        ("record_set", [I32, I32, I32], []),
        ("record_get", [I32, I32, I32], [I32]),
        ("record_find", [I32, I32], [I32]),
        ("attr", [I32, I32, I32], [I32]),
        # Alpha 10: closures / first-class functions
        ("box_new", [I32], [I32]),
        ("make_fn", [I32, I32, I32, I32], [I32]),
        ("call_fn", [I32, I32, I32, I32], [I32]),
    ]

    def _build_helpers(self):
        self.hf = {}
        for name, ps, rs in self.HELPER_SIGS:
            self._helper(name, ps, rs)
        for name, ps, rs in self.BUILTIN_SIGS:
            self._helper("b_" + name, [I32] + ps, rs)
        self._h_alloc()
        self._h_make_number()
        self._h_make_yesno()
        self._h_make_result()
        self._h_make_text()
        self._h_text_concat()
        self._h_concat3()
        self._h_text_eq()
        self._h_text_cmp()
        self._h_text_find()
        self._h_utf8_len()
        self._h_utf8_byte_offset()
        self._h_sb()
        self._h_fmt_nested()
        self._h_list_record_text()
        self._h_to_text()
        self._h_py_str()
        self._h_truthy()
        self._h_equals()
        self._h_list_eq()
        self._h_record_eq()
        self._h_panic_line()
        self._h_to_int()
        self._h_in_op()
        self._h_binary()
        self._h_unary()
        self._h_lists()
        self._h_list_misc()
        self._h_index()
        self._h_records()
        self._h_boxes()
        self._h_builtins()

    # -- core runtime helpers -----------------------------------------------
    def _h_alloc(self):
        f = self._helper("alloc", [I32], [I32])
        w = f.w
        n = f.new_local()
        old = f.new_local()
        new = f.new_local()
        # n = (size + 3) & ~3
        w.local_get(0); w.i32_const(3); w.i32_add()
        w.i32_const(-4); w.i32_and(); w.local_set(n)
        w.global_get(self.heap_g); w.local_tee(old)
        w.local_get(n); w.i32_add(); w.local_set(new)
        # if new > memory.size * 65536: grow
        w.local_get(new)
        w.memory_size(); w.i32_const(16); w.i32_shl()
        w.i32_gt_u()
        l = w.if_()
        # pages = ((new - bytes) + 65535) >> 16, at least 1
        w.local_get(new)
        w.memory_size(); w.i32_const(16); w.i32_shl(); w.i32_sub()
        w.i32_const(65535); w.i32_add()
        w.i32_const(16); w.i32_shr_u()
        w.local_tee(n)
        w.i32_const(1); w.i32_lt_u()
        l2 = w.if_()
        w.i32_const(1); w.local_set(n)
        w.end()
        w.local_get(n); w.memory_grow()
        w.i32_const(-1); w.i32_eq()
        l3 = w.if_()
        self.emit_panic(w, "Out of memory.")
        w.end(); w.end()
        w.local_get(new); w.global_set(self.heap_g)
        w.local_get(old)

    def _h_make_number(self):
        f = self._helper("make_number", [F64], [I32])
        w = f.w
        p = f.new_local()
        w.i32_const(12); w.call(self.h["alloc"]); w.local_tee(p)
        w.i32_const(TAG_NUMBER); w.i32_store(0)
        w.local_get(p); w.local_get(0); w.f64_store(4)
        w.local_get(p)

    def _h_make_yesno(self):
        # make_yesno(i32) -> yes/no static value
        f = self._helper("make_yesno", [I32], [I32])
        w = f.w
        w.i32_const(self.yes_addr)
        w.i32_const(self.no_addr)
        w.local_get(0)
        w.select()

    def _h_make_result(self):
        # make_result(payload, is_ok) -> result value
        f = self._helper("make_result", [I32, I32], [I32])
        w = f.w
        p = f.new_local()
        w.i32_const(12); w.call(self.h["alloc"]); w.local_tee(p)
        w.i32_const(TAG_RESULT); w.i32_store(0)
        w.local_get(p); w.local_get(1); w.i32_store(4)
        w.local_get(p); w.local_get(0); w.i32_store(8)
        w.local_get(p)

    def _h_make_text(self):
        # make_text(ptr, len) -> text value (copies bytes)
        f = self._helper("make_text", [I32, I32], [I32])
        w = f.w
        p = f.new_local()
        dp = f.new_local()
        w.i32_const(12); w.call(self.h["alloc"]); w.local_tee(p)
        w.i32_const(TAG_TEXT); w.i32_store(0)
        w.local_get(1); w.call(self.h["alloc"]); w.local_set(dp)
        w.local_get(dp); w.local_get(0); w.local_get(1); w.memory_copy()
        w.local_get(p); w.local_get(dp); w.i32_store(4)
        w.local_get(p); w.local_get(1); w.i32_store(8)
        w.local_get(p)

    def _h_text_concat(self):
        f = self._helper("text_concat", [I32, I32], [I32])
        w = f.w
        la = f.new_local(); lb = f.new_local()
        pa = f.new_local(); pb = f.new_local()
        p = f.new_local(); dp = f.new_local()
        w.local_get(0); w.i32_load(4); w.local_set(pa)
        w.local_get(0); w.i32_load(8); w.local_set(la)
        w.local_get(1); w.i32_load(4); w.local_set(pb)
        w.local_get(1); w.i32_load(8); w.local_set(lb)
        w.i32_const(12); w.call(self.h["alloc"]); w.local_tee(p)
        w.i32_const(TAG_TEXT); w.i32_store(0)
        w.local_get(la); w.local_get(lb); w.i32_add()
        w.call(self.h["alloc"]); w.local_set(dp)
        w.local_get(dp); w.local_get(pa); w.local_get(la); w.memory_copy()
        w.local_get(dp); w.local_get(la); w.i32_add()
        w.local_get(pb); w.local_get(lb); w.memory_copy()
        w.local_get(p); w.local_get(dp); w.i32_store(4)
        w.local_get(p); w.local_get(la); w.local_get(lb); w.i32_add()
        w.i32_store(8)
        w.local_get(p)

    def _h_concat3(self):
        f = self._helper("concat3", [I32, I32, I32], [I32])
        w = f.w
        w.local_get(0); w.local_get(1); w.call(self.h["text_concat"])
        w.local_get(2); w.call(self.h["text_concat"])

    def _h_text_eq(self):
        f = self._helper("text_eq", [I32, I32], [I32])
        w = f.w
        la = f.new_local(); lb = f.new_local()
        pa = f.new_local(); pb = f.new_local()
        i = f.new_local()
        w.local_get(0); w.i32_load(8); w.local_set(la)
        w.local_get(1); w.i32_load(8); w.local_set(lb)
        w.local_get(la); w.local_get(lb); w.i32_ne()
        l = w.if_()
        w.i32_const(0); w.return_()
        w.end()
        w.local_get(0); w.i32_load(4); w.local_set(pa)
        w.local_get(1); w.i32_load(4); w.local_set(pb)
        w.i32_const(0); w.local_set(i)
        top = w.loop()
        w.local_get(i); w.local_get(la); w.i32_ge_u()
        l2 = w.if_()
        w.i32_const(1); w.return_()
        w.end()
        w.local_get(pa); w.local_get(i); w.i32_add(); w.i32_load8_u()
        w.local_get(pb); w.local_get(i); w.i32_add(); w.i32_load8_u()
        w.i32_ne()
        l3 = w.if_()
        w.i32_const(0); w.return_()
        w.end()
        w.local_get(i); w.i32_const(1); w.i32_add(); w.local_set(i)
        w.br(top)
        w.end()
        w.i32_const(1)

    def _h_text_cmp(self):
        # -> -1, 0, 1 (unsigned byte lexicographic order)
        f = self._helper("text_cmp", [I32, I32], [I32])
        w = f.w
        la = f.new_local(); lb = f.new_local()
        pa = f.new_local(); pb = f.new_local()
        i = f.new_local(); m = f.new_local()
        w.local_get(0); w.i32_load(4); w.local_set(pa)
        w.local_get(0); w.i32_load(8); w.local_set(la)
        w.local_get(1); w.i32_load(4); w.local_set(pb)
        w.local_get(1); w.i32_load(8); w.local_set(lb)
        # m = min(la, lb)
        w.local_get(la); w.local_get(lb); w.i32_lt_u()
        l = w.if_()
        w.local_get(la); w.local_set(m)
        w.else_()
        w.local_get(lb); w.local_set(m)
        w.end()
        w.i32_const(0); w.local_set(i)
        top = w.loop()
        w.local_get(i); w.local_get(m); w.i32_ge_u()
        l2 = w.if_()
        # all equal so far: compare lengths
        w.local_get(la); w.local_get(lb); w.i32_eq()
        l3 = w.if_()
        w.i32_const(0); w.return_()
        w.end()
        w.local_get(la); w.local_get(lb); w.i32_lt_u()
        l4 = w.if_()
        w.i32_const(-1); w.return_()
        w.end()
        w.i32_const(1); w.return_()
        w.end()
        w.local_get(pa); w.local_get(i); w.i32_add(); w.i32_load8_u()
        w.local_get(pb); w.local_get(i); w.i32_add(); w.i32_load8_u()
        w.i32_ne()
        l5 = w.if_()
        w.local_get(pa); w.local_get(i); w.i32_add(); w.i32_load8_u()
        w.local_get(pb); w.local_get(i); w.i32_add(); w.i32_load8_u()
        w.i32_lt_u()
        l6 = w.if_()
        w.i32_const(-1); w.return_()
        w.end()
        w.i32_const(1); w.return_()
        w.end()
        w.local_get(i); w.i32_const(1); w.i32_add(); w.local_set(i)
        w.br(top)
        w.end()
        w.i32_const(0)

    def _h_text_find(self):
        # text_find(hay, needle) -> index or -1
        f = self._helper("text_find", [I32, I32], [I32])
        w = f.w
        lh = f.new_local(); ln = f.new_local()
        ph = f.new_local(); pn = f.new_local()
        i = f.new_local(); j = f.new_local()
        w.local_get(0); w.i32_load(4); w.local_set(ph)
        w.local_get(0); w.i32_load(8); w.local_set(lh)
        w.local_get(1); w.i32_load(4); w.local_set(pn)
        w.local_get(1); w.i32_load(8); w.local_set(ln)
        w.i32_const(0); w.local_set(i)
        outer = w.loop()
        # if i + ln > lh: return -1
        w.local_get(i); w.local_get(ln); w.i32_add()
        w.local_get(lh); w.i32_gt_u()
        l = w.if_()
        w.i32_const(-1); w.return_()
        w.end()
        w.i32_const(0); w.local_set(j)
        inner = w.loop()
        w.local_get(j); w.local_get(ln); w.i32_ge_u()
        l2 = w.if_()
        w.local_get(i); w.return_()
        w.end()
        w.local_get(ph); w.local_get(i); w.i32_add(); w.local_get(j); w.i32_add()
        w.i32_load8_u()
        w.local_get(pn); w.local_get(j); w.i32_add(); w.i32_load8_u()
        w.i32_ne()
        l3 = w.if_()
        # mismatch: next i
        w.local_get(i); w.i32_const(1); w.i32_add(); w.local_set(i)
        w.br(outer)
        w.end()
        w.local_get(j); w.i32_const(1); w.i32_add(); w.local_set(j)
        w.br(inner)
        w.end()
        w.end()
        w.i32_const(-1)

    def _h_utf8_len(self):
        # utf8_len(ptr, len) -> code point count
        f = self._helper("utf8_len", [I32, I32], [I32])
        w = f.w
        i = f.new_local(); n = f.new_local()
        w.i32_const(0); w.local_set(i)
        w.i32_const(0); w.local_set(n)
        top = w.loop()
        w.local_get(i); w.local_get(1); w.i32_ge_u()
        l = w.if_()
        w.local_get(n); w.return_()
        w.end()
        w.local_get(0); w.local_get(i); w.i32_add(); w.i32_load8_u()
        w.i32_const(0xC0); w.i32_and()
        w.i32_const(0x80); w.i32_ne()
        l2 = w.if_()
        w.local_get(n); w.i32_const(1); w.i32_add(); w.local_set(n)
        w.end()
        w.local_get(i); w.i32_const(1); w.i32_add(); w.local_set(i)
        w.br(top)
        w.end()
        w.local_get(n)

    def _h_utf8_byte_offset(self):
        # utf8_byte_offset(ptr, len, charidx) -> byte offset of char idx
        f = self._helper("utf8_byte_offset", [I32, I32, I32], [I32])
        w = f.w
        i = f.new_local(); c = f.new_local(); b0 = f.new_local()
        w.i32_const(0); w.local_set(i)
        w.i32_const(0); w.local_set(c)
        top = w.loop()
        w.local_get(c); w.local_get(2); w.i32_ge_s()
        l = w.if_()
        w.local_get(i); w.return_()
        w.end()
        w.local_get(i); w.local_get(1); w.i32_ge_u()
        l2 = w.if_()
        w.local_get(i); w.return_()
        w.end()
        w.local_get(0); w.local_get(i); w.i32_add(); w.i32_load8_u()
        w.local_set(b0)
        # sz = 1/2/3/4 by leading byte
        w.local_get(b0); w.i32_const(0x80); w.i32_lt_u()
        l3 = w.if_()
        w.i32_const(1); w.local_set(b0)
        w.else_()
        w.local_get(b0); w.i32_const(0xE0); w.i32_lt_u()
        l4 = w.if_()
        w.i32_const(2); w.local_set(b0)
        w.else_()
        w.local_get(b0); w.i32_const(0xF0); w.i32_lt_u()
        l5 = w.if_()
        w.i32_const(3); w.local_set(b0)
        w.else_()
        w.i32_const(4); w.local_set(b0)
        w.end(); w.end(); w.end()
        w.local_get(i); w.local_get(b0); w.i32_add(); w.local_set(i)
        w.local_get(c); w.i32_const(1); w.i32_add(); w.local_set(c)
        w.br(top)
        w.end()
        w.local_get(i)

    def _h_sb(self):
        # String builder: struct [bufptr, len, cap]. sb_new/push/finish.
        f = self._helper("sb_new", [], [I32])
        w = f.w
        p = f.new_local(); dp = f.new_local()
        w.i32_const(12); w.call(self.h["alloc"]); w.local_set(p)
        w.local_get(p); w.i32_const(0); w.i32_store(4)
        w.local_get(p); w.i32_const(0); w.i32_store(8)
        w.i32_const(16); w.call(self.h["alloc"]); w.local_set(dp)
        w.local_get(p); w.local_get(dp); w.i32_store(0)
        w.local_get(p); w.i32_const(16); w.i32_store(8)
        w.local_get(p)

        f = self._helper("sb_push", [I32, I32, I32], [])
        w = f.w
        bl = f.new_local(); cap = f.new_local(); nb = f.new_local()
        ndp = f.new_local()
        # bl = len, cap = cap
        w.local_get(0); w.i32_load(4); w.local_set(bl)
        w.local_get(0); w.i32_load(8); w.local_set(cap)
        # if bl + addlen > cap: grow
        w.local_get(bl); w.local_get(2); w.i32_add()
        w.local_get(cap); w.i32_gt_u()
        l = w.if_()
        # nb = max(cap*2, bl+addlen)
        w.local_get(cap); w.i32_const(1); w.i32_shl()
        w.local_tee(nb)
        w.local_get(bl); w.local_get(2); w.i32_add()
        w.i32_lt_u()
        l2 = w.if_()
        w.local_get(bl); w.local_get(2); w.i32_add(); w.local_set(nb)
        w.end()
        w.local_get(nb); w.call(self.h["alloc"]); w.local_set(ndp)
        w.local_get(ndp)
        w.local_get(0); w.i32_load(0)
        w.local_get(bl); w.memory_copy()
        w.local_get(0); w.local_get(ndp); w.i32_store(0)
        w.local_get(0); w.local_get(nb); w.i32_store(8)
        w.end()
        # copy new bytes at buf+bl
        w.local_get(0); w.i32_load(0); w.local_get(bl); w.i32_add()
        w.local_get(1); w.local_get(2); w.memory_copy()
        w.local_get(0)
        w.local_get(bl); w.local_get(2); w.i32_add()
        w.i32_store(4)

        f = self._helper("sb_push_text", [I32, I32], [])
        w = f.w
        w.local_get(0)
        w.local_get(1); w.i32_load(4)
        w.local_get(1); w.i32_load(8)
        w.call(self.h["sb_push"])

        f = self._helper("sb_push_lit", [I32, I32, I32], [])
        w = f.w
        w.local_get(0); w.local_get(1); w.local_get(2)
        w.call(self.h["sb_push"])

        f = self._helper("sb_finish", [I32], [I32])
        w = f.w
        w.local_get(0); w.i32_load(0)
        w.local_get(0); w.i32_load(4)
        w.call(self.h["make_text"])

    def _h_to_text(self):
        # to_text(v) -> text value, mirroring runtime.fmt
        f = self._helper("to_text", [I32], [I32])
        w = f.w
        tag = f.new_local()
        w.local_get(0); w.i32_load(0); w.local_set(tag)
        # text -> identity
        w.local_get(tag); w.i32_const(TAG_TEXT); w.i32_eq()
        l = w.if_()
        w.local_get(0); w.return_()
        w.end()
        # number -> host formatting
        w.local_get(tag); w.i32_const(TAG_NUMBER); w.i32_eq()
        l = w.if_()
        w.i32_const(SCRATCH)
        w.local_get(0); w.f64_load(4); w.call(self.imp["fmt_num"])
        w.call(self.h["make_text"])
        w.return_()
        w.end()
        # yes/no
        w.local_get(tag); w.i32_const(TAG_YESNO); w.i32_eq()
        l = w.if_()
        w.local_get(0); w.i32_load(4)
        l2 = w.if_()
        w.i32_const(self.yes_text); w.return_()
        w.end()
        w.i32_const(self.no_text); w.return_()
        w.end()
        # nothing
        w.local_get(tag); w.i32_const(TAG_NOTHING); w.i32_eq()
        l = w.if_()
        w.i32_const(self.nothing_text); w.return_()
        w.end()
        # list
        w.local_get(tag); w.i32_const(TAG_LIST); w.i32_eq()
        l = w.if_()
        w.local_get(0); w.i32_const(1); w.call(self.h["list_text"]); w.return_()
        w.end()
        # record
        w.local_get(tag); w.i32_const(TAG_RECORD); w.i32_eq()
        l = w.if_()
        w.local_get(0); w.i32_const(1); w.call(self.h["record_text"]); w.return_()
        w.end()
        # result
        w.local_get(tag); w.i32_const(TAG_RESULT); w.i32_eq()
        l = w.if_()
        w.local_get(0); w.i32_load(4)  # is_ok
        l2 = w.if_()
        w.i32_const(self.text_val("ok("))
        w.local_get(0); w.i32_load(8); w.call(self.h["to_text"])
        w.i32_const(self.text_val(")"))
        w.call(self.h["concat3"]); w.return_()
        w.end()
        w.i32_const(self.text_val('error("'))
        w.local_get(0); w.i32_load(8)
        w.i32_const(self.text_val('")'))
        w.call(self.h["concat3"]); w.return_()
        w.end()
        # function -> function "name" (simple source name, like the VM)
        w.local_get(tag); w.i32_const(TAG_FUNCTION); w.i32_eq()
        l = w.if_()
        w.i32_const(self.text_val('function "'))
        w.local_get(0); w.i32_load(8)
        w.i32_const(self.text_val('"'))
        w.call(self.h["concat3"]); w.return_()
        w.end()
        w.unreachable()

    def _h_fmt_nested(self):
        # fmt_nested(v) -> text, mirroring runtime.fmt_nested:
        # strings get JSON-quoted, everything else uses fmt (to_text).
        f = self._helper("fmt_nested", [I32], [I32])
        w = f.w
        w.local_get(0); w.i32_load(0); w.i32_const(TAG_TEXT); w.i32_eq()
        l = w.if_()
        w.i32_const(self.text_val('"')); w.local_get(0)
        w.i32_const(self.text_val('"')); w.call(self.h["concat3"]); w.return_()
        w.end()
        w.local_get(0); w.call(self.h["to_text"]); w.return_()

    def _h_list_record_text(self):
        # list_text(v, quoted): "[" + ", ".join(elem) + "]".
        # quoted=0 uses to_text (fmt), quoted=1 uses fmt_nested (quoted strings).
        f = self._helper("list_text", [I32, I32], [I32])
        w = f.w
        sb = f.new_local(); n = f.new_local(); i = f.new_local(); t = f.new_local()
        w.call(self.h["sb_new"]); w.local_set(sb)
        w.local_get(sb); w.i32_const(self.lit_bytes(b"[")); w.i32_const(1)
        w.call(self.h["sb_push"])
        w.local_get(0); w.i32_load(8); w.local_set(n)
        w.i32_const(0); w.local_set(i)
        blk = w.block()
        top = w.loop()
        w.local_get(i); w.local_get(n); w.i32_ge_u(); w.br_if(blk)
        w.local_get(i)
        l = w.if_()
        w.local_get(sb); w.i32_const(self.lit_bytes(b", ")); w.i32_const(2)
        w.call(self.h["sb_push"])
        w.end()
        w.local_get(0); w.local_get(i); w.call(self.h["list_get0"])
        w.local_set(t)
        # t = quoted ? fmt_nested(t) : to_text(t)
        w.local_get(1)
        l = w.if_()
        w.local_get(t); w.call(self.h["fmt_nested"]); w.local_set(t)
        w.else_()
        w.local_get(t); w.call(self.h["to_text"]); w.local_set(t)
        w.end()
        w.local_get(sb); w.local_get(t); w.call(self.h["sb_push_text"])
        w.local_get(i); w.i32_const(1); w.i32_add(); w.local_set(i)
        w.br(top)
        w.end(); w.end()
        w.local_get(sb); w.i32_const(self.lit_bytes(b"]")); w.i32_const(1)
        w.call(self.h["sb_push"])
        w.local_get(sb); w.call(self.h["sb_finish"])

        # record_text(v, quoted): "{" + "k: v, ..." + "}"
        f = self._helper("record_text", [I32, I32], [I32])
        w = f.w
        sb = f.new_local(); n = f.new_local(); i = f.new_local()
        t = f.new_local(); base = f.new_local()
        w.call(self.h["sb_new"]); w.local_set(sb)
        w.local_get(sb); w.i32_const(self.lit_bytes(b"{")); w.i32_const(1)
        w.call(self.h["sb_push"])
        w.local_get(0); w.i32_load(4); w.local_set(base)
        w.local_get(0); w.i32_load(8); w.local_set(n)
        w.i32_const(0); w.local_set(i)
        blk = w.block()
        top = w.loop()
        w.local_get(i); w.local_get(n); w.i32_ge_u(); w.br_if(blk)
        w.local_get(i)
        l = w.if_()
        w.local_get(sb); w.i32_const(self.lit_bytes(b", ")); w.i32_const(2)
        w.call(self.h["sb_push"])
        w.end()
        # key text value at base + i*8 (keys are never quoted, like VM fmt)
        w.local_get(base); w.local_get(i); w.i32_const(3); w.i32_shl(); w.i32_add()
        w.i32_load(0); w.local_set(t)
        w.local_get(sb); w.local_get(t); w.call(self.h["sb_push_text"])
        w.local_get(sb); w.i32_const(self.lit_bytes(b": ")); w.i32_const(2)
        w.call(self.h["sb_push"])
        w.local_get(base); w.local_get(i); w.i32_const(3); w.i32_shl(); w.i32_add()
        w.i32_load(4); w.local_set(t)
        w.local_get(1)
        l = w.if_()
        w.local_get(t); w.call(self.h["fmt_nested"]); w.local_set(t)
        w.else_()
        w.local_get(t); w.call(self.h["to_text"]); w.local_set(t)
        w.end()
        w.local_get(sb); w.local_get(t); w.call(self.h["sb_push_text"])
        w.local_get(i); w.i32_const(1); w.i32_add(); w.local_set(i)
        w.br(top)
        w.end(); w.end()
        w.local_get(sb); w.i32_const(self.lit_bytes(b"}")); w.i32_const(1)
        w.call(self.h["sb_push"])
        w.local_get(sb); w.call(self.h["sb_finish"])

    def _h_py_str(self):
        # py_str(v, quoted) -> text value with Python str() semantics,
        # used by text() and join. quoted=1 quotes text values (inside
        # lists/records). Numbers use fmt_num: integer-valued floats render
        # as plain integers, matching the VM's text() on ints, the native
        # backend, and say. (WASM numbers are uniformly f64, so the VM's
        # int/float distinction — text(20)="20" vs text(20.0)="20.0" — is
        # not representable; the integer rendering wins.)
        f = self._helper("py_str", [I32, I32], [I32])
        w = f.w
        tag = f.new_local(); fv = f.new_local(F64)
        w.local_get(0); w.i32_load(0); w.local_set(tag)
        # number
        w.local_get(tag); w.i32_const(TAG_NUMBER); w.i32_eq()
        l = w.if_()
        w.local_get(0); w.f64_load(4); w.local_set(fv)
        w.i32_const(SCRATCH); w.local_get(fv); w.call(self.imp["fmt_num"])
        w.call(self.h["make_text"]); w.return_()
        w.end()
        # text
        w.local_get(tag); w.i32_const(TAG_TEXT); w.i32_eq()
        l = w.if_()
        w.local_get(1); w.i32_eqz()
        l2 = w.if_()
        w.local_get(0); w.return_()
        w.end()
        w.i32_const(self.text_val('"')); w.local_get(0); w.i32_const(self.text_val('"'))
        w.call(self.h["concat3"]); w.return_()
        w.end()
        # yes -> "True", no -> "False"
        w.local_get(tag); w.i32_const(TAG_YESNO); w.i32_eq()
        l = w.if_()
        w.local_get(0); w.i32_load(4)
        l2 = w.if_()
        w.i32_const(self.text_val("True")); w.return_()
        w.end()
        w.i32_const(self.text_val("False")); w.return_()
        w.end()
        # nothing -> "None"
        w.local_get(tag); w.i32_const(TAG_NOTHING); w.i32_eq()
        l = w.if_()
        w.i32_const(self.text_val("None")); w.return_()
        w.end()
        # list / record with quoted=1 inside
        w.local_get(tag); w.i32_const(TAG_LIST); w.i32_eq()
        l = w.if_()
        w.local_get(0); w.i32_const(1); w.call(self.h["list_text"]); w.return_()
        w.end()
        w.local_get(tag); w.i32_const(TAG_RECORD); w.i32_eq()
        l = w.if_()
        w.local_get(0); w.i32_const(1); w.call(self.h["record_text"]); w.return_()
        w.end()
        # result and anything else: fall back to fmt
        w.local_get(0); w.call(self.h["to_text"])

    def _h_panic_line(self):
        # panic_line(line, msgptr, msglen): panic with "Line <n>: <msg>".
        f = self._helper("panic_line", [I32, I32, I32], [])
        w = f.w
        t = f.new_local()
        w.i32_const(SCRATCH)
        w.local_get(0); w.f64_convert_i32_s()
        w.call(self.imp["fmt_num"])
        w.call(self.h["make_text"]); w.local_set(t)
        w.i32_const(self.text_val("Line ")); w.local_get(t)
        w.call(self.h["text_concat"]); w.local_set(t)
        w.local_get(t); w.i32_const(self.text_val(": "))
        w.call(self.h["text_concat"]); w.local_set(t)
        w.local_get(t); w.local_get(1); w.local_get(2)
        w.call(self.h["make_text"])
        w.call(self.h["text_concat"]); w.local_set(t)
        w.local_get(t); w.i32_load(4)
        w.local_get(t); w.i32_load(8)
        w.call(self.imp["panic"])
        w.unreachable()

    def emit_panic_line(self, w, line, msg):
        addr = self.lit_bytes(msg.encode("utf-8"))
        w.i32_const(line); w.i32_const(addr); w.i32_const(len(msg.encode("utf-8")))
        w.call(self.h["panic_line"])
        w.unreachable()

    def emit_yesno(self, w):
        # cond:i32 on stack -> yes/no value
        l = w.if_()
        w.i32_const(self.yes_addr); w.return_()
        w.end()
        w.i32_const(self.no_addr)

    def _h_truthy(self):
        f = self._helper("truthy", [I32], [I32])
        w = f.w
        tag = f.new_local()
        w.local_get(0); w.i32_load(0); w.local_set(tag)
        w.local_get(tag); w.i32_const(TAG_NOTHING); w.i32_eq()
        l = w.if_(); w.i32_const(0); w.return_(); w.end()
        w.local_get(tag); w.i32_const(TAG_NUMBER); w.i32_eq()
        l = w.if_()
        w.local_get(0); w.f64_load(4); w.f64_const(0.0); w.f64_ne(); w.return_()
        w.end()
        w.local_get(tag); w.i32_const(TAG_TEXT); w.i32_eq()
        l = w.if_()
        w.local_get(0); w.i32_load(8); w.i32_const(0); w.i32_ne(); w.return_()
        w.end()
        w.local_get(tag); w.i32_const(TAG_YESNO); w.i32_eq()
        l = w.if_()
        w.local_get(0); w.i32_load(4); w.return_()
        w.end()
        w.local_get(tag); w.i32_const(TAG_LIST); w.i32_eq()
        l = w.if_()
        w.local_get(0); w.i32_load(8); w.i32_const(0); w.i32_ne(); w.return_()
        w.end()
        w.local_get(tag); w.i32_const(TAG_RECORD); w.i32_eq()
        l = w.if_()
        w.local_get(0); w.i32_load(8); w.i32_const(0); w.i32_ne(); w.return_()
        w.end()
        w.i32_const(1)  # result values are always truthy

    def _h_equals(self):
        f = self._helper("equals", [I32, I32], [I32])
        w = f.w
        ta = f.new_local(); tb = f.new_local()
        w.local_get(0); w.i32_load(0); w.local_set(ta)
        w.local_get(1); w.i32_load(0); w.local_set(tb)
        # number == yes/no like Python (1 == True)
        w.local_get(ta); w.i32_const(TAG_NUMBER); w.i32_eq()
        w.local_get(tb); w.i32_const(TAG_YESNO); w.i32_eq()
        w.i32_and()
        l = w.if_()
        w.local_get(0); w.f64_load(4)
        w.local_get(1); w.i32_load(4); w.f64_convert_i32_s()
        w.f64_eq(); w.return_()
        w.end()
        w.local_get(ta); w.i32_const(TAG_YESNO); w.i32_eq()
        w.local_get(tb); w.i32_const(TAG_NUMBER); w.i32_eq()
        w.i32_and()
        l = w.if_()
        w.local_get(0); w.i32_load(4); w.f64_convert_i32_s()
        w.local_get(1); w.f64_load(4)
        w.f64_eq(); w.return_()
        w.end()
        w.local_get(ta); w.local_get(tb); w.i32_ne()
        l = w.if_(); w.i32_const(0); w.return_(); w.end()
        w.local_get(ta); w.i32_const(TAG_NOTHING); w.i32_eq()
        l = w.if_(); w.i32_const(1); w.return_(); w.end()
        w.local_get(ta); w.i32_const(TAG_NUMBER); w.i32_eq()
        l = w.if_()
        w.local_get(0); w.f64_load(4); w.local_get(1); w.f64_load(4)
        w.f64_eq(); w.return_()
        w.end()
        w.local_get(ta); w.i32_const(TAG_YESNO); w.i32_eq()
        l = w.if_()
        w.local_get(0); w.i32_load(4); w.local_get(1); w.i32_load(4)
        w.i32_eq(); w.return_()
        w.end()
        w.local_get(ta); w.i32_const(TAG_TEXT); w.i32_eq()
        l = w.if_()
        w.local_get(0); w.local_get(1); w.call(self.h["text_eq"]); w.return_()
        w.end()
        w.local_get(ta); w.i32_const(TAG_LIST); w.i32_eq()
        l = w.if_()
        w.local_get(0); w.local_get(1); w.call(self.h["list_eq"]); w.return_()
        w.end()
        w.local_get(ta); w.i32_const(TAG_RECORD); w.i32_eq()
        l = w.if_()
        w.local_get(0); w.local_get(1); w.call(self.h["record_eq"]); w.return_()
        w.end()
        # Alpha 10: functions compare by identity (same value pointer), like
        # the VM's Python-object equality for VMFunction.
        w.local_get(ta); w.i32_const(TAG_FUNCTION); w.i32_eq()
        l = w.if_()
        w.local_get(0); w.local_get(1); w.i32_eq(); w.return_()
        w.end()
        # result
        w.local_get(0); w.i32_load(4); w.local_get(1); w.i32_load(4); w.i32_ne()
        l = w.if_(); w.i32_const(0); w.return_(); w.end()
        w.local_get(0); w.i32_load(4)
        l = w.if_()
        w.local_get(0); w.i32_load(8); w.local_get(1); w.i32_load(8)
        w.call(self.h["equals"]); w.return_()
        w.end()
        w.local_get(0); w.i32_load(8); w.local_get(1); w.i32_load(8)
        w.call(self.h["text_eq"])

    def _h_list_eq(self):
        f = self._helper("list_eq", [I32, I32], [I32])
        w = f.w
        n = f.new_local(); i = f.new_local()
        w.local_get(0); w.i32_load(8); w.local_set(n)
        w.local_get(1); w.i32_load(8); w.local_get(n); w.i32_ne()
        l = w.if_(); w.i32_const(0); w.return_(); w.end()
        w.i32_const(0); w.local_set(i)
        blk = w.block(); top = w.loop()
        w.local_get(i); w.local_get(n); w.i32_ge_u(); w.br_if(blk)
        w.local_get(0); w.local_get(i); w.call(self.h["list_get0"])
        w.local_get(1); w.local_get(i); w.call(self.h["list_get0"])
        w.call(self.h["equals"]); w.i32_eqz()
        l = w.if_(); w.i32_const(0); w.return_(); w.end()
        w.local_get(i); w.i32_const(1); w.i32_add(); w.local_set(i)
        w.br(top)
        w.end(); w.end()
        w.i32_const(1)

    def _h_record_eq(self):
        f = self._helper("record_eq", [I32, I32], [I32])
        w = f.w
        n = f.new_local(); i = f.new_local(); j = f.new_local()
        base = f.new_local()
        w.local_get(0); w.i32_load(8); w.local_set(n)
        w.local_get(1); w.i32_load(8); w.local_get(n); w.i32_ne()
        l = w.if_(); w.i32_const(0); w.return_(); w.end()
        w.local_get(0); w.i32_load(4); w.local_set(base)
        w.i32_const(0); w.local_set(i)
        blk = w.block(); top = w.loop()
        w.local_get(i); w.local_get(n); w.i32_ge_u(); w.br_if(blk)
        # j = record_find(b, key_i)
        w.local_get(1)
        w.local_get(base); w.local_get(i); w.i32_const(3); w.i32_shl(); w.i32_add()
        w.i32_load(0)
        w.call(self.h["record_find"]); w.local_set(j)
        w.local_get(j); w.i32_const(0); w.i32_lt_s()
        l = w.if_(); w.i32_const(0); w.return_(); w.end()
        w.local_get(base); w.local_get(i); w.i32_const(3); w.i32_shl(); w.i32_add()
        w.i32_load(4)
        w.local_get(1); w.i32_load(4); w.local_get(j); w.i32_const(3); w.i32_shl()
        w.i32_add(); w.i32_load(4)
        w.call(self.h["equals"]); w.i32_eqz()
        l = w.if_(); w.i32_const(0); w.return_(); w.end()
        w.local_get(i); w.i32_const(1); w.i32_add(); w.local_set(i)
        w.br(top)
        w.end(); w.end()
        w.i32_const(1)

    def panic_line_local(self, w, line_local, msg):
        # panic_line where the line number comes from a local at runtime.
        b = msg.encode("utf-8")
        addr = self.lit_bytes(b)
        w.local_get(line_local); w.i32_const(addr); w.i32_const(len(b))
        w.call(self.h["panic_line"])
        w.unreachable()

    def _h_to_int(self):
        # to_int(line, v) -> i32 (for repeat counts, indices)
        f = self._helper("to_int", [I32, I32], [I32])
        w = f.w
        tag = f.new_local(); fv = f.new_local(F64); ln = f.new_local()
        w.local_get(0); w.local_set(ln)
        w.local_get(1); w.i32_load(0); w.local_set(tag)
        blk = w.block()
        w.local_get(tag); w.i32_const(TAG_NUMBER); w.i32_eq()
        l = w.if_()
        w.local_get(1); w.f64_load(4); w.local_set(fv)
        w.br(blk)
        w.end()
        w.local_get(tag); w.i32_const(TAG_TEXT); w.i32_eq()
        l = w.if_()
        w.local_get(1); w.i32_load(4); w.local_get(1); w.i32_load(8)
        w.call(self.imp["parse_num"]); w.local_set(fv)
        w.local_get(fv); w.local_get(fv); w.f64_ne()
        l2 = w.if_()
        self.panic_line_local(w, ln, "I can't use that text as a whole number.")
        w.end()
        w.br(blk)
        w.end()
        self.panic_line_local(w, ln, "I can't use that value as a whole number.")
        w.end()  # blk
        w.local_get(fv); w.local_get(fv); w.f64_ne()
        w.local_get(fv); w.f64_const(2147483647.0); w.f64_gt(); w.i32_or()
        w.local_get(fv); w.f64_const(-2147483648.0); w.f64_lt(); w.i32_or()
        l = w.if_()
        self.panic_line_local(w, ln, "that number is too big to use as a whole number.")
        w.end()
        w.local_get(fv); w.i32_trunc_f64_s()

    def _h_in_op(self):
        # in_op(line, a, b) -> i32 (1 if a is in b)
        f = self._helper("in_op", [I32, I32, I32], [I32])
        w = f.w
        tb = f.new_local(); n = f.new_local(); i = f.new_local(); ln = f.new_local()
        w.local_get(0); w.local_set(ln)
        w.local_get(2); w.i32_load(0); w.local_set(tb)
        w.local_get(tb); w.i32_const(TAG_TEXT); w.i32_eq()
        l = w.if_()
        w.local_get(1); w.i32_load(0); w.i32_const(TAG_TEXT); w.i32_ne()
        l2 = w.if_()
        self.panic_line_local(w, ln, "I can't look for that inside text.")
        w.end()
        w.local_get(2); w.local_get(1); w.call(self.h["text_find"])
        w.i32_const(-1); w.i32_ne(); w.return_()
        w.end()
        w.local_get(tb); w.i32_const(TAG_LIST); w.i32_eq()
        l = w.if_()
        w.local_get(2); w.i32_load(8); w.local_set(n)
        w.i32_const(0); w.local_set(i)
        blk = w.block(); top = w.loop()
        w.local_get(i); w.local_get(n); w.i32_ge_u(); w.br_if(blk)
        w.local_get(1)
        w.local_get(2); w.local_get(i); w.call(self.h["list_get0"])
        w.call(self.h["equals"])
        l2 = w.if_()
        w.i32_const(1); w.return_()
        w.end()
        w.local_get(i); w.i32_const(1); w.i32_add(); w.local_set(i)
        w.br(top)
        w.end(); w.end()
        w.i32_const(0); w.return_()
        w.end()
        w.local_get(tb); w.i32_const(TAG_RECORD); w.i32_eq()
        l = w.if_()
        w.local_get(1); w.i32_load(0); w.i32_const(TAG_TEXT); w.i32_ne()
        l2 = w.if_()
        w.i32_const(0); w.return_()
        w.end()
        w.local_get(2); w.local_get(1); w.call(self.h["record_find"])
        w.i32_const(-1); w.i32_ne(); w.return_()
        w.end()
        self.panic_line_local(w, ln, "I can't look inside that value.")
        w.unreachable()

    # binary op codes
    OP_ADD, OP_SUB, OP_MUL, OP_DIV, OP_MOD, OP_POW = 0, 1, 2, 3, 4, 5
    OP_EQ, OP_NE, OP_LT, OP_LE, OP_GT, OP_GE = 6, 7, 8, 9, 10, 11
    OP_AND, OP_OR, OP_IN = 12, 13, 14

    def _h_binary(self):
        # binary(line, op, a, b) -> value
        f = self._helper("binary", [I32, I32, I32, I32], [I32])
        w = f.w
        op = f.new_local(); ta = f.new_local(); tb = f.new_local()
        fa = f.new_local(F64); fb = f.new_local(F64); ln = f.new_local()
        w.local_get(0); w.local_set(ln)
        w.local_get(1); w.local_set(op)
        w.local_get(2); w.i32_load(0); w.local_set(ta)
        w.local_get(3); w.i32_load(0); w.local_set(tb)
        # and / or
        w.local_get(op); w.i32_const(self.OP_AND); w.i32_eq()
        l = w.if_()
        w.local_get(2); w.call(self.h["truthy"])
        w.local_get(3); w.call(self.h["truthy"])
        w.i32_and(); self.emit_yesno(w); w.return_()
        w.end()
        w.local_get(op); w.i32_const(self.OP_OR); w.i32_eq()
        l = w.if_()
        w.local_get(2); w.call(self.h["truthy"])
        w.local_get(3); w.call(self.h["truthy"])
        w.i32_or(); self.emit_yesno(w); w.return_()
        w.end()
        w.local_get(op); w.i32_const(self.OP_IN); w.i32_eq()
        l = w.if_()
        w.local_get(ln); w.local_get(2); w.local_get(3)
        w.call(self.h["in_op"]); self.emit_yesno(w); w.return_()
        w.end()
        # == / !=
        w.local_get(op); w.i32_const(self.OP_EQ); w.i32_eq()
        w.local_get(op); w.i32_const(self.OP_NE); w.i32_eq()
        w.i32_or()
        l = w.if_()
        w.local_get(2); w.local_get(3); w.call(self.h["equals"])
        w.local_get(op); w.i32_const(self.OP_NE); w.i32_eq(); w.i32_xor()
        self.emit_yesno(w); w.return_()
        w.end()
        # ADD
        w.local_get(op); w.i32_const(self.OP_ADD); w.i32_eq()
        l = w.if_()
        w.local_get(ta); w.i32_const(TAG_NUMBER); w.i32_eq()
        w.local_get(tb); w.i32_const(TAG_NUMBER); w.i32_eq()
        w.i32_and()
        l2 = w.if_()
        w.local_get(2); w.f64_load(4); w.local_get(3); w.f64_load(4)
        w.f64_add(); w.call(self.h["make_number"]); w.return_()
        w.end()
        w.local_get(ta); w.i32_const(TAG_TEXT); w.i32_eq()
        w.local_get(tb); w.i32_const(TAG_TEXT); w.i32_eq()
        w.i32_and()
        l2 = w.if_()
        w.local_get(2); w.local_get(3); w.call(self.h["text_concat"]); w.return_()
        w.end()
        self.panic_line_local(w, ln, "I can't add those values together.")
        w.end()
        # numeric ops: SUB MUL DIV MOD POW
        w.local_get(op); w.i32_const(self.OP_SUB); w.i32_ge_u()
        w.local_get(op); w.i32_const(self.OP_POW); w.i32_le_u()
        w.i32_and()
        l = w.if_()
        w.local_get(ta); w.i32_const(TAG_NUMBER); w.i32_ne()
        w.local_get(tb); w.i32_const(TAG_NUMBER); w.i32_ne()
        w.i32_or()
        l2 = w.if_()
        self.panic_line_local(w, ln, "I need numbers for that operation.")
        w.end()
        w.local_get(2); w.f64_load(4); w.local_set(fa)
        w.local_get(3); w.f64_load(4); w.local_set(fb)
        # DIV: exact VM message, no line prefix
        w.local_get(op); w.i32_const(self.OP_DIV); w.i32_eq()
        l2 = w.if_()
        w.local_get(fb); w.f64_const(0.0); w.f64_eq()
        l3 = w.if_()
        self.emit_panic(w, "You cannot divide by zero.")
        w.end()
        w.local_get(fa); w.local_get(fb); w.f64_div()
        w.call(self.h["make_number"]); w.return_()
        w.end()
        # MOD: Python semantics
        w.local_get(op); w.i32_const(self.OP_MOD); w.i32_eq()
        l2 = w.if_()
        w.local_get(fb); w.f64_const(0.0); w.f64_eq()
        l3 = w.if_()
        self.panic_line_local(w, ln, "integer division or modulo by zero")
        w.end()
        # r = fa - trunc(fa/fb)*fb; if r != 0 and sign(r) != sign(fb): r += fb
        w.local_get(fa)
        w.local_get(fa); w.local_get(fb); w.f64_div(); w.f64_trunc()
        w.local_get(fb); w.f64_mul()
        w.f64_sub()
        w.local_set(fa)  # fa = r
        w.local_get(fa); w.f64_const(0.0); w.f64_ne()
        w.local_get(fa); w.f64_const(0.0); w.f64_lt()
        w.local_get(fb); w.f64_const(0.0); w.f64_lt()
        w.i32_xor()
        w.i32_and()
        l3 = w.if_()
        w.local_get(fa); w.local_get(fb); w.f64_add(); w.local_set(fa)
        w.end()
        w.local_get(fa); w.call(self.h["make_number"]); w.return_()
        w.end()
        # SUB / MUL / POW
        w.local_get(op); w.i32_const(self.OP_SUB); w.i32_eq()
        l2 = w.if_()
        w.local_get(fa); w.local_get(fb); w.f64_sub()
        w.call(self.h["make_number"]); w.return_()
        w.end()
        w.local_get(op); w.i32_const(self.OP_MUL); w.i32_eq()
        l2 = w.if_()
        w.local_get(fa); w.local_get(fb); w.f64_mul()
        w.call(self.h["make_number"]); w.return_()
        w.end()
        w.local_get(fa); w.local_get(fb); w.call(self.imp["pow"])
        w.call(self.h["make_number"]); w.return_()
        w.end()
        # comparisons LT LE GT GE on numbers or text
        w.local_get(ta); w.i32_const(TAG_NUMBER); w.i32_eq()
        w.local_get(tb); w.i32_const(TAG_NUMBER); w.i32_eq()
        w.i32_and()
        l = w.if_()
        w.local_get(2); w.f64_load(4); w.local_set(fa)
        w.local_get(3); w.f64_load(4); w.local_set(fb)
        w.local_get(op); w.i32_const(self.OP_LT); w.i32_eq()
        l2 = w.if_()
        w.local_get(fa); w.local_get(fb); w.f64_lt(); self.emit_yesno(w); w.return_()
        w.end()
        w.local_get(op); w.i32_const(self.OP_LE); w.i32_eq()
        l2 = w.if_()
        w.local_get(fa); w.local_get(fb); w.f64_le(); self.emit_yesno(w); w.return_()
        w.end()
        w.local_get(op); w.i32_const(self.OP_GT); w.i32_eq()
        l2 = w.if_()
        w.local_get(fa); w.local_get(fb); w.f64_gt(); self.emit_yesno(w); w.return_()
        w.end()
        w.local_get(fa); w.local_get(fb); w.f64_ge(); self.emit_yesno(w); w.return_()
        w.end()
        w.local_get(ta); w.i32_const(TAG_TEXT); w.i32_eq()
        w.local_get(tb); w.i32_const(TAG_TEXT); w.i32_eq()
        w.i32_and()
        l = w.if_()
        w.local_get(2); w.local_get(3); w.call(self.h["text_cmp"])
        w.local_set(tb)  # tb no longer needed; reuse as cmp result
        w.local_get(1); w.i32_const(self.OP_LT); w.i32_eq()
        l2 = w.if_()
        w.local_get(tb); w.i32_const(0); w.i32_lt_s()
        self.emit_yesno(w); w.return_()
        w.end()
        w.local_get(1); w.i32_const(self.OP_LE); w.i32_eq()
        l2 = w.if_()
        w.local_get(tb); w.i32_const(0); w.i32_le_s()
        self.emit_yesno(w); w.return_()
        w.end()
        w.local_get(1); w.i32_const(self.OP_GT); w.i32_eq()
        l2 = w.if_()
        w.local_get(tb); w.i32_const(0); w.i32_gt_s()
        self.emit_yesno(w); w.return_()
        w.end()
        w.local_get(tb); w.i32_const(0); w.i32_ge_s()
        self.emit_yesno(w); w.return_()
        w.end()
        self.panic_line_local(w, ln, "I can't compare those values.")
        w.unreachable()

    def _h_unary(self):
        # unary(line, op, v): op 0=not, 1=neg
        f = self._helper("unary", [I32, I32, I32], [I32])
        w = f.w
        w.local_get(1); w.i32_const(0); w.i32_eq()
        l = w.if_()
        w.local_get(2); w.call(self.h["truthy"]); w.i32_eqz()
        self.emit_yesno(w); w.return_()
        w.end()
        w.local_get(2); w.i32_load(0); w.i32_const(TAG_NUMBER); w.i32_ne()
        l = w.if_()
        self.panic_line_local(w, 0, "I can't negate that value.")
        w.end()
        w.local_get(2); w.f64_load(4); w.f64_neg()
        w.call(self.h["make_number"])

    def _h_lists(self):
        # list_new() -> empty list (cap 4)
        f = self._helper("list_new", [], [I32])
        w = f.w
        p = f.new_local(); dp = f.new_local()
        w.i32_const(16); w.call(self.h["alloc"]); w.local_tee(p)
        w.i32_const(TAG_LIST); w.i32_store(0)
        w.i32_const(16); w.call(self.h["alloc"]); w.local_set(dp)
        w.local_get(p); w.local_get(dp); w.i32_store(4)
        w.local_get(p); w.i32_const(0); w.i32_store(8)
        w.local_get(p); w.i32_const(4); w.i32_store(12)
        w.local_get(p)

        # list_push(l, v) -> l (grows as needed)
        f = self._helper("list_push", [I32, I32], [I32])
        w = f.w
        ln = f.new_local(); cap = f.new_local(); base = f.new_local()
        w.local_get(0); w.i32_load(8); w.local_set(ln)
        w.local_get(0); w.i32_load(12); w.local_set(cap)
        w.local_get(0); w.i32_load(4); w.local_set(base)
        w.local_get(ln); w.local_get(cap); w.i32_ge_u()
        l = w.if_()
        # newcap = max(cap*2, 4); realloc
        w.local_get(cap); w.i32_const(1); w.i32_shl()
        w.local_tee(cap)
        w.i32_const(4); w.i32_lt_u()
        l2 = w.if_()
        w.i32_const(4); w.local_set(cap)
        w.end()
        w.local_get(cap); w.i32_const(2); w.i32_shl()
        w.call(self.h["alloc"])
        w.local_tee(base)
        w.local_get(0); w.i32_load(4)
        w.local_get(ln); w.i32_const(2); w.i32_shl()
        w.memory_copy()
        w.local_get(0); w.local_get(base); w.i32_store(4)
        w.local_get(0); w.local_get(cap); w.i32_store(12)
        w.end()
        w.local_get(base); w.local_get(ln); w.i32_const(2); w.i32_shl(); w.i32_add()
        w.local_get(1); w.i32_store(0)
        w.local_get(0)
        w.local_get(ln); w.i32_const(1); w.i32_add(); w.i32_store(8)
        w.local_get(0)

        f = self._helper("list_get0", [I32, I32], [I32])
        w = f.w
        w.local_get(0); w.i32_load(4)
        w.local_get(1); w.i32_const(2); w.i32_shl(); w.i32_add()
        w.i32_load(0)

        f = self._helper("list_len", [I32], [I32])
        w = f.w
        w.local_get(0); w.i32_load(8)

    def emit_missing_key(self, w, ln_local, key_local, tmp_local):
        # panic_line(ln, "'" + key + "'") mirroring Python's KeyError
        w.i32_const(self.text_val("'")); w.local_get(key_local)
        w.i32_const(self.text_val("'"))
        w.call(self.h["concat3"]); w.local_set(tmp_local)
        w.local_get(ln_local)
        w.local_get(tmp_local); w.i32_load(4)
        w.local_get(tmp_local); w.i32_load(8)
        w.call(self.h["panic_line"])
        w.unreachable()

    def _h_index(self):
        # index_get(line, obj, idx) -> value
        f = self._helper("index_get", [I32, I32, I32], [I32])
        w = f.w
        ln = f.new_local(); to = f.new_local(); i = f.new_local()
        n = f.new_local(); p = f.new_local(); ci = f.new_local()
        bo = f.new_local(); bl = f.new_local()
        w.local_get(0); w.local_set(ln)
        w.local_get(1); w.i32_load(0); w.local_set(to)
        # list: 1-based (negative wraps like the VM)
        w.local_get(to); w.i32_const(TAG_LIST); w.i32_eq()
        l = w.if_()
        w.local_get(ln); w.local_get(2); w.call(self.h["to_int"])
        w.i32_const(1); w.i32_sub(); w.local_set(i)
        w.local_get(1); w.i32_load(8); w.local_set(n)
        w.local_get(i); w.i32_const(0); w.i32_lt_s()
        l2 = w.if_()
        w.local_get(i); w.local_get(n); w.i32_add(); w.local_set(i)
        w.end()
        w.local_get(i); w.i32_const(0); w.i32_lt_s()
        w.local_get(i); w.local_get(n); w.i32_ge_s()
        w.i32_or()
        l2 = w.if_()
        self.panic_line_local(w, ln, "list index out of range")
        w.end()
        w.local_get(1); w.local_get(i); w.call(self.h["list_get0"]); w.return_()
        w.end()
        # text: 0-based like the VM
        w.local_get(to); w.i32_const(TAG_TEXT); w.i32_eq()
        l = w.if_()
        w.local_get(ln); w.local_get(2); w.call(self.h["to_int"]); w.local_set(ci)
        w.local_get(1); w.i32_load(4); w.local_set(p)
        w.local_get(1); w.i32_load(8); w.local_set(n)
        w.local_get(p); w.local_get(n); w.call(self.h["utf8_len"]); w.local_set(i)
        w.local_get(ci); w.i32_const(0); w.i32_lt_s()
        l2 = w.if_()
        w.local_get(ci); w.local_get(i); w.i32_add(); w.local_set(ci)
        w.end()
        w.local_get(ci); w.i32_const(0); w.i32_lt_s()
        w.local_get(ci); w.local_get(i); w.i32_ge_s()
        w.i32_or()
        l2 = w.if_()
        self.panic_line_local(w, ln, "string index out of range")
        w.end()
        w.local_get(p); w.local_get(n); w.local_get(ci)
        w.call(self.h["utf8_byte_offset"]); w.local_set(bo)
        w.local_get(p); w.local_get(n); w.local_get(ci); w.i32_const(1); w.i32_add()
        w.call(self.h["utf8_byte_offset"]); w.local_set(bl)
        w.local_get(bl); w.local_get(bo); w.i32_sub(); w.local_set(bl)
        w.local_get(p); w.local_get(bo); w.i32_add()
        w.local_get(bl); w.call(self.h["make_text"]); w.return_()
        w.end()
        # record: key lookup
        w.local_get(to); w.i32_const(TAG_RECORD); w.i32_eq()
        l = w.if_()
        w.local_get(2); w.i32_load(0); w.i32_const(TAG_TEXT); w.i32_ne()
        l2 = w.if_()
        self.panic_line_local(w, ln, "I can't use that as a key.")
        w.end()
        w.local_get(1); w.local_get(2); w.call(self.h["record_find"]); w.local_set(i)
        w.local_get(i); w.i32_const(0); w.i32_lt_s()
        l2 = w.if_()
        self.emit_missing_key(w, ln, 2, n)
        w.end()
        w.local_get(1); w.i32_load(4)
        w.local_get(i); w.i32_const(3); w.i32_shl(); w.i32_add()
        w.i32_load(4); w.return_()
        w.end()
        self.panic_line_local(w, ln, "I can't index into that value.")
        w.unreachable()

        # index_set(line, obj, idx, val)
        f = self._helper("index_set", [I32, I32, I32, I32], [])
        w = f.w
        ln = f.new_local(); to = f.new_local(); i = f.new_local()
        n = f.new_local(); tname = f.new_local()
        w.local_get(0); w.local_set(ln)
        w.local_get(1); w.i32_load(0); w.local_set(to)
        w.local_get(to); w.i32_const(TAG_LIST); w.i32_eq()
        l = w.if_()
        w.local_get(ln); w.local_get(2); w.call(self.h["to_int"])
        w.i32_const(1); w.i32_sub(); w.local_set(i)
        w.local_get(1); w.i32_load(8); w.local_set(n)
        w.local_get(i); w.i32_const(0); w.i32_lt_s()
        l2 = w.if_()
        w.local_get(i); w.local_get(n); w.i32_add(); w.local_set(i)
        w.end()
        w.local_get(i); w.i32_const(0); w.i32_lt_s()
        w.local_get(i); w.local_get(n); w.i32_ge_s()
        w.i32_or()
        l2 = w.if_()
        self.panic_line_local(w, ln, "list index out of range")
        w.end()
        w.local_get(1); w.i32_load(4)
        w.local_get(i); w.i32_const(2); w.i32_shl(); w.i32_add()
        w.local_get(3); w.i32_store(0)
        w.return_()
        w.end()
        w.local_get(to); w.i32_const(TAG_RECORD); w.i32_eq()
        l = w.if_()
        w.local_get(2); w.i32_load(0); w.i32_const(TAG_TEXT); w.i32_ne()
        l2 = w.if_()
        self.panic_line_local(w, ln, "I can't use that as a key.")
        w.end()
        w.local_get(1); w.local_get(2); w.local_get(3)
        w.call(self.h["record_set"])
        w.return_()
        w.end()
        # mirror the VM's direct (line-less) messages
        w.local_get(2); w.i32_load(0); w.i32_const(TAG_NUMBER); w.i32_eq()
        l = w.if_()
        self.emit_panic(w, "That position isn't in the list (positions start at 1).")
        w.end()
        w.i32_const(self.text_val("NoneType")); w.local_set(tname)
        w.local_get(to); w.i32_const(TAG_NUMBER); w.i32_eq()
        l = w.if_(); w.i32_const(self.text_val("float")); w.local_set(tname); w.end()
        w.local_get(to); w.i32_const(TAG_TEXT); w.i32_eq()
        l = w.if_(); w.i32_const(self.text_val("str")); w.local_set(tname); w.end()
        w.local_get(to); w.i32_const(TAG_YESNO); w.i32_eq()
        l = w.if_(); w.i32_const(self.text_val("bool")); w.local_set(tname); w.end()
        w.local_get(to); w.i32_const(TAG_LIST); w.i32_eq()
        l = w.if_(); w.i32_const(self.text_val("list")); w.local_set(tname); w.end()
        w.local_get(to); w.i32_const(TAG_RESULT); w.i32_eq()
        l = w.if_(); w.i32_const(self.text_val("NikoResult")); w.local_set(tname); w.end()
        w.i32_const(self.text_val("Cannot set a key on ")); w.local_get(tname)
        w.i32_const(self.text_val(".")); w.call(self.h["concat3"])
        w.local_tee(tname)
        w.i32_load(4); w.local_get(tname); w.i32_load(8)
        w.call(self.imp["panic"])
        w.unreachable()

    def _h_records(self):
        # record_new() -> empty record
        f = self._helper("record_new", [], [I32])
        w = f.w
        p = f.new_local(); dp = f.new_local()
        w.i32_const(16); w.call(self.h["alloc"]); w.local_tee(p)
        w.i32_const(TAG_RECORD); w.i32_store(0)
        w.i32_const(32); w.call(self.h["alloc"]); w.local_set(dp)
        w.local_get(p); w.local_get(dp); w.i32_store(4)
        w.local_get(p); w.i32_const(0); w.i32_store(8)
        w.local_get(p); w.i32_const(4); w.i32_store(12)
        w.local_get(p)

        # record_find(r, keytext) -> entry index or -1
        f = self._helper("record_find", [I32, I32], [I32])
        w = f.w
        n = f.new_local(); i = f.new_local(); base = f.new_local()
        w.local_get(0); w.i32_load(8); w.local_set(n)
        w.local_get(0); w.i32_load(4); w.local_set(base)
        w.i32_const(0); w.local_set(i)
        blk = w.block(); top = w.loop()
        w.local_get(i); w.local_get(n); w.i32_ge_u(); w.br_if(blk)
        w.local_get(base); w.local_get(i); w.i32_const(3); w.i32_shl(); w.i32_add()
        w.i32_load(0)
        w.local_get(1); w.call(self.h["text_eq"])
        l = w.if_()
        w.local_get(i); w.return_()
        w.end()
        w.local_get(i); w.i32_const(1); w.i32_add(); w.local_set(i)
        w.br(top)
        w.end(); w.end()
        w.i32_const(-1)

        # record_set(r, k, v)
        f = self._helper("record_set", [I32, I32, I32], [])
        w = f.w
        j = f.new_local(); n = f.new_local(); cap = f.new_local(); base = f.new_local()
        w.local_get(0); w.local_get(1); w.call(self.h["record_find"]); w.local_set(j)
        w.local_get(j); w.i32_const(0); w.i32_ge_s()
        l = w.if_()
        w.local_get(0); w.i32_load(4)
        w.local_get(j); w.i32_const(3); w.i32_shl(); w.i32_add()
        w.local_get(2); w.i32_store(4)
        w.return_()
        w.end()
        w.local_get(0); w.i32_load(8); w.local_set(n)
        w.local_get(0); w.i32_load(12); w.local_set(cap)
        w.local_get(0); w.i32_load(4); w.local_set(base)
        w.local_get(n); w.local_get(cap); w.i32_ge_u()
        l = w.if_()
        w.local_get(cap); w.i32_const(1); w.i32_shl(); w.local_tee(cap)
        w.i32_const(3); w.i32_shl()
        w.call(self.h["alloc"])
        w.local_tee(base)
        w.local_get(0); w.i32_load(4)
        w.local_get(n); w.i32_const(3); w.i32_shl()
        w.memory_copy()
        w.local_get(0); w.local_get(base); w.i32_store(4)
        w.local_get(0); w.local_get(cap); w.i32_store(12)
        w.end()
        w.local_get(base); w.local_get(n); w.i32_const(3); w.i32_shl(); w.i32_add()
        w.local_get(1); w.i32_store(0)
        w.local_get(base); w.local_get(n); w.i32_const(3); w.i32_shl(); w.i32_add()
        w.local_get(2); w.i32_store(4)
        w.local_get(0)
        w.local_get(n); w.i32_const(1); w.i32_add(); w.i32_store(8)

        # record_get(line, r, k) -> value
        f = self._helper("record_get", [I32, I32, I32], [I32])
        w = f.w
        j = f.new_local()
        w.local_get(1); w.local_get(2); w.call(self.h["record_find"]); w.local_set(j)
        w.local_get(j); w.i32_const(0); w.i32_lt_s()
        l = w.if_()
        self.emit_missing_key(w, 0, 2, j)
        w.end()
        w.local_get(1); w.i32_load(4)
        w.local_get(j); w.i32_const(3); w.i32_shl(); w.i32_add()
        w.i32_load(4)

        # attr(line, obj, keytextval) -> value (record attribute access)
        f = self._helper("attr", [I32, I32, I32], [I32])
        w = f.w
        w.local_get(1); w.i32_load(0); w.i32_const(TAG_RECORD); w.i32_eq()
        l = w.if_()
        w.local_get(0); w.local_get(1); w.local_get(2)
        w.call(self.h["record_get"]); w.return_()
        w.end()
        self.panic_line_local(w, 0, "I can't get that attribute from that value.")
        w.unreachable()

    # (name, params-after-line, results) for b_<name> builtins.
    BUILTIN_SIGS = [
        ("length", [I32], [I32]),
        ("text", [I32], [I32]),
        ("number", [I32], [I32]),
        ("item_of", [I32, I32], [I32]),
        ("upper", [I32], [I32]),
        ("lower", [I32], [I32]),
        ("trim", [I32], [I32]),
        ("replace", [I32, I32, I32], [I32]),
        ("split", [I32, I32], [I32]),
        ("join", [I32, I32], [I32]),
        ("has", [I32, I32], [I32]),
        ("sum", [I32], [I32]),
        ("average", [I32], [I32]),
        ("abs", [I32], [I32]),
        ("ceil", [I32], [I32]),
        ("floor", [I32], [I32]),
        ("round", [I32], [I32]),
        ("sqrt", [I32], [I32]),
        ("max", [I32], [I32]),
        ("min", [I32], [I32]),
        ("sorted", [I32], [I32]),
        ("reversed", [I32], [I32]),
        ("unique", [I32], [I32]),
        ("keys", [I32], [I32]),
        ("starts_with", [I32, I32], [I32]),
        ("ends_with", [I32, I32], [I32]),
        ("count_of", [I32, I32], [I32]),
        ("niko_range", [I32, I32], [I32]),
        ("ok", [I32], [I32]),
        ("error", [I32], [I32]),
        ("is_ok", [I32], [I32]),
        ("is_error", [I32], [I32]),
        ("unwrap", [I32], [I32]),
        ("unwrap_or", [I32, I32], [I32]),
        ("error_message", [I32], [I32]),
        ("try_number", [I32], [I32]),
        ("random_int", [I32, I32], [I32]),
        ("pick", [I32], [I32]),
        ("today", [], [I32]),
        ("now", [], [I32]),
        ("sleep", [I32], [I32]),
    ]

    def _bh(self, name):
        return self.hf["b_" + name]

    def _h_boxes(self):
        # Alpha 10: closures + first-class functions.
        #
        # A cell is 8 heap bytes holding one boxed-value pointer; sharing the
        # cell pointer is capture by reference. A function value is 20 bytes:
        # [tag=7, table_idx, name_ptr (text), nparams, env_ptr]. env_ptr
        # points at ncaptures*4 heap bytes holding cell pointers (0 = none).
        f = self._helper("box_new", [I32], [I32])
        w = f.w
        c = f.new_local()
        w.i32_const(8); w.call(self.h["alloc"]); w.local_set(c)
        w.local_get(c); w.local_get(0); w.i32_store(0)
        w.local_get(c)

        f = self._helper("make_fn", [I32, I32, I32, I32], [I32])
        w = f.w
        v = f.new_local()
        w.i32_const(20); w.call(self.h["alloc"]); w.local_set(v)
        w.local_get(v); w.i32_const(TAG_FUNCTION); w.i32_store(0)
        w.local_get(v); w.local_get(0); w.i32_store(4)
        w.local_get(v); w.local_get(1); w.i32_store(8)
        w.local_get(v); w.local_get(2); w.i32_store(12)
        w.local_get(v); w.local_get(3); w.i32_store(16)
        w.local_get(v)

        # call_fn(line, fn, nargs, args_ptr): every call site goes through
        # here. Tag check -> `I can't call <v> as a function.`; arity check ->
        # `<name> expected <p> arguments, got <n>.`; then call_indirect with
        # the uniform (env_ptr, nargs, args_ptr) signature.
        f = self._helper("call_fn", [I32, I32, I32, I32], [I32])
        w = f.w
        sb = f.new_local(); tv = f.new_local()
        # not a function value -> trap
        w.local_get(1); w.i32_load(0); w.i32_const(TAG_FUNCTION); w.i32_ne()
        l = w.if_()
        w.call(self.h["sb_new"]); w.local_set(sb)
        w.local_get(sb); w.i32_const(self.text_val("I can't call "))
        w.call(self.h["sb_push_text"])
        w.local_get(sb); w.local_get(1); w.call(self.h["to_text"])
        w.call(self.h["sb_push_text"])
        w.local_get(sb); w.i32_const(self.text_val(" as a function."))
        w.call(self.h["sb_push_text"])
        w.local_get(sb); w.call(self.h["sb_finish"]); w.local_set(tv)
        w.local_get(0); w.local_get(tv); w.i32_load(4)
        w.local_get(tv); w.i32_load(8)
        w.call(self.h["panic_line"])
        w.unreachable()
        w.end()
        # arity check: nargs (param 2) vs nparams (fn[12])
        w.local_get(2); w.local_get(1); w.i32_load(12); w.i32_ne()
        l = w.if_()
        w.call(self.h["sb_new"]); w.local_set(sb)
        w.local_get(sb); w.local_get(1); w.i32_load(8)
        w.call(self.h["sb_push_text"])
        w.local_get(sb); w.i32_const(self.text_val(" expected "))
        w.call(self.h["sb_push_text"])
        w.local_get(sb); w.local_get(1); w.i32_load(12)
        w.f64_convert_i32_s(); w.call(self.h["make_number"])
        w.call(self.h["to_text"]); w.call(self.h["sb_push_text"])
        w.local_get(sb); w.i32_const(self.text_val(" arguments, got "))
        w.call(self.h["sb_push_text"])
        w.local_get(sb); w.local_get(2)
        w.f64_convert_i32_s(); w.call(self.h["make_number"])
        w.call(self.h["to_text"]); w.call(self.h["sb_push_text"])
        w.local_get(sb); w.i32_const(self.text_val("."))
        w.call(self.h["sb_push_text"])
        w.local_get(sb); w.call(self.h["sb_finish"]); w.local_set(tv)
        w.local_get(0); w.local_get(tv); w.i32_load(4)
        w.local_get(tv); w.i32_load(8)
        w.call(self.h["panic_line"])
        w.unreachable()
        w.end()
        # indirect call: stack [env_ptr, nargs, args_ptr, table_idx]
        w.local_get(1); w.i32_load(16)
        w.local_get(2)
        w.local_get(3)
        w.local_get(1); w.i32_load(4)
        w.call_indirect(self.niko_fn_type)

    def _h_builtins(self):
        self._b_length(); self._b_text(); self._b_number(); self._b_item_of()
        self._b_case(); self._b_trim(); self._b_replace(); self._b_split()
        self._b_join(); self._b_has(); self._b_sum_avg(); self._b_math1()
        self._b_max_min(); self._b_sorted(); self._b_reversed()
        self._b_unique(); self._b_keys(); self._b_starts_ends()
        self._b_count_of(); self._b_range(); self._b_results()
        self._b_random_pick(); self._b_time()

    def _b_length(self):
        f = self._bh("length"); w = f.w
        tag = f.new_local()
        w.local_get(1); w.i32_load(0); w.local_set(tag)
        w.local_get(tag); w.i32_const(TAG_TEXT); w.i32_eq()
        l = w.if_()
        w.local_get(1); w.i32_load(4); w.local_get(1); w.i32_load(8)
        w.call(self.h["utf8_len"]); w.f64_convert_i32_s()
        w.call(self.h["make_number"]); w.return_()
        w.end()
        w.local_get(tag); w.i32_const(TAG_LIST); w.i32_eq()
        w.local_get(tag); w.i32_const(TAG_RECORD); w.i32_eq()
        w.i32_or()
        l = w.if_()
        w.local_get(1); w.i32_load(8); w.f64_convert_i32_s()
        w.call(self.h["make_number"]); w.return_()
        w.end()
        self.panic_line_local(w, 0, "I can't get the length of that value.")
        w.unreachable()

    def _b_text(self):
        # text(x) is Python str(x): yes -> "True", nothing -> "None";
        # integer-valued numbers render without ".0" (see _h_py_str).
        f = self._bh("text"); w = f.w
        w.local_get(1); w.i32_const(0); w.call(self.h["py_str"])

    def _b_number(self):
        f = self._bh("number"); w = f.w
        tag = f.new_local(); fv = f.new_local(F64); t = f.new_local()
        w.local_get(1); w.i32_load(0); w.local_set(tag)
        w.local_get(tag); w.i32_const(TAG_NUMBER); w.i32_eq()
        l = w.if_()
        # number(x) is int(x): truncates toward zero like Python
        w.local_get(0); w.local_get(1); w.call(self.h["to_int"])
        w.f64_convert_i32_s(); w.call(self.h["make_number"]); w.return_()
        w.end()
        w.local_get(tag); w.i32_const(TAG_YESNO); w.i32_eq()
        l = w.if_()
        w.local_get(1); w.i32_load(4); w.f64_convert_i32_s()
        w.call(self.h["make_number"]); w.return_()
        w.end()
        w.local_get(tag); w.i32_const(TAG_TEXT); w.i32_eq()
        l = w.if_()
        w.local_get(1); w.i32_load(4); w.local_get(1); w.i32_load(8)
        w.call(self.imp["parse_num"]); w.local_set(fv)
        w.local_get(fv); w.local_get(fv); w.f64_ne()
        l2 = w.if_()
        # "I can't turn '<s>' into a number." (exact VM message, no line)
        w.i32_const(self.text_val("I can't turn '"))
        w.local_get(1)
        w.i32_const(self.text_val("' into a number."))
        w.call(self.h["concat3"]); w.local_set(t)
        w.local_get(t); w.i32_load(4); w.local_get(t); w.i32_load(8)
        w.call(self.imp["panic"]); w.unreachable()
        w.end()
        w.local_get(fv); w.call(self.h["make_number"]); w.return_()
        w.end()
        # other types: "I can't turn <repr> into a number." — Python str() form
        w.local_get(1); w.i32_const(0); w.call(self.h["py_str"]); w.local_set(t)
        w.i32_const(self.text_val("I can't turn "))
        w.local_get(t)
        w.i32_const(self.text_val(" into a number."))
        w.call(self.h["concat3"]); w.local_set(t)
        w.local_get(t); w.i32_load(4); w.local_get(t); w.i32_load(8)
        w.call(self.imp["panic"]); w.unreachable()

    def _b_item_of(self):
        # item_of(i, x): 1-based like the VM's x[int(i)-1]
        f = self._bh("item_of"); w = f.w
        ln = f.new_local(); to = f.new_local(); j = f.new_local()
        n = f.new_local(); p = f.new_local(); bo = f.new_local(); bl = f.new_local()
        blen = f.new_local()  # byte length (n becomes the char count below)
        w.local_get(0); w.local_set(ln)
        w.local_get(2); w.i32_load(0); w.local_set(to)
        w.local_get(ln); w.local_get(1); w.call(self.h["to_int"])
        w.i32_const(1); w.i32_sub(); w.local_set(j)
        w.local_get(to); w.i32_const(TAG_LIST); w.i32_eq()
        l = w.if_()
        w.local_get(2); w.i32_load(8); w.local_set(n)
        w.local_get(j); w.i32_const(0); w.i32_lt_s()
        l2 = w.if_()
        w.local_get(j); w.local_get(n); w.i32_add(); w.local_set(j)
        w.end()
        w.local_get(j); w.i32_const(0); w.i32_lt_s()
        w.local_get(j); w.local_get(n); w.i32_ge_s(); w.i32_or()
        l2 = w.if_()
        self.panic_line_local(w, ln, "list index out of range")
        w.end()
        w.local_get(2); w.local_get(j); w.call(self.h["list_get0"]); w.return_()
        w.end()
        w.local_get(to); w.i32_const(TAG_TEXT); w.i32_eq()
        l = w.if_()
        w.local_get(2); w.i32_load(4); w.local_set(p)
        w.local_get(2); w.i32_load(8); w.local_set(blen)
        w.local_get(p); w.local_get(blen); w.call(self.h["utf8_len"]); w.local_set(n)
        w.local_get(j); w.i32_const(0); w.i32_lt_s()
        l2 = w.if_()
        w.local_get(j); w.local_get(n); w.i32_add(); w.local_set(j)
        w.end()
        w.local_get(j); w.i32_const(0); w.i32_lt_s()
        w.local_get(j); w.local_get(n); w.i32_ge_s(); w.i32_or()
        l2 = w.if_()
        self.panic_line_local(w, ln, "string index out of range")
        w.end()
        w.local_get(p); w.local_get(blen); w.local_get(j)
        w.call(self.h["utf8_byte_offset"]); w.local_set(bo)
        w.local_get(p); w.local_get(blen); w.local_get(j); w.i32_const(1); w.i32_add()
        w.call(self.h["utf8_byte_offset"]); w.local_set(bl)
        w.local_get(bl); w.local_get(bo); w.i32_sub(); w.local_set(bl)
        w.local_get(p); w.local_get(bo); w.i32_add()
        w.local_get(bl); w.call(self.h["make_text"]); w.return_()
        w.end()
        self.panic_line_local(w, ln, "I can't pick that out of that value.")
        w.unreachable()

    def _b_case(self):
        # upper / lower share the shape; ASCII only (documented)
        for name, lo, hi, delta in (("upper", 0x61, 0x7A, -32),
                                    ("lower", 0x41, 0x5A, 32)):
            f = self._bh(name); w = f.w
            t = f.new_local(); p = f.new_local(); ln2 = f.new_local()
            i = f.new_local(); np = f.new_local(); b = f.new_local()
            w.local_get(1); w.call(self.h["to_text"]); w.local_set(t)
            w.local_get(t); w.i32_load(4); w.local_set(p)
            w.local_get(t); w.i32_load(8); w.local_set(ln2)
            w.local_get(ln2); w.call(self.h["alloc"]); w.local_set(np)
            w.i32_const(0); w.local_set(i)
            blk = w.block(); top = w.loop()
            w.local_get(i); w.local_get(ln2); w.i32_ge_u(); w.br_if(blk)
            w.local_get(p); w.local_get(i); w.i32_add(); w.i32_load8_u()
            w.local_set(b)
            w.local_get(b); w.i32_const(lo); w.i32_ge_u()
            w.local_get(b); w.i32_const(hi); w.i32_le_u()
            w.i32_and()
            l = w.if_()
            w.local_get(b); w.i32_const(delta); w.i32_add(); w.local_set(b)
            w.end()
            w.local_get(np); w.local_get(i); w.i32_add()
            w.local_get(b); w.i32_store8()
            w.local_get(i); w.i32_const(1); w.i32_add(); w.local_set(i)
            w.br(top)
            w.end(); w.end()
            w.local_get(np); w.local_get(ln2); w.call(self.h["make_text"])

    def _b_trim(self):
        f = self._bh("trim"); w = f.w
        t = f.new_local(); p = f.new_local(); l = f.new_local()
        s = f.new_local(); e = f.new_local(); b = f.new_local()
        w.local_get(1); w.call(self.h["to_text"]); w.local_set(t)
        w.local_get(t); w.i32_load(4); w.local_set(p)
        w.local_get(t); w.i32_load(8); w.local_set(l)
        w.i32_const(0); w.local_set(s)
        # skip leading whitespace
        blk = w.block(); top = w.loop()
        w.local_get(s); w.local_get(l); w.i32_ge_u(); w.br_if(blk)
        w.local_get(p); w.local_get(s); w.i32_add(); w.i32_load8_u(); w.local_set(b)
        # is b whitespace? space, \t..\r
        w.local_get(b); w.i32_const(0x20); w.i32_eq()
        w.local_get(b); w.i32_const(0x09); w.i32_ge_u()
        w.local_get(b); w.i32_const(0x0D); w.i32_le_u()
        w.i32_and(); w.i32_or()
        w.i32_eqz(); w.br_if(blk)
        w.local_get(s); w.i32_const(1); w.i32_add(); w.local_set(s)
        w.br(top)
        w.end(); w.end()
        w.local_get(l); w.local_set(e)
        blk = w.block(); top = w.loop()
        w.local_get(e); w.local_get(s); w.i32_le_u(); w.br_if(blk)
        w.local_get(p); w.local_get(e); w.i32_const(1); w.i32_sub(); w.i32_add()
        w.i32_load8_u(); w.local_set(b)
        w.local_get(b); w.i32_const(0x20); w.i32_eq()
        w.local_get(b); w.i32_const(0x09); w.i32_ge_u()
        w.local_get(b); w.i32_const(0x0D); w.i32_le_u()
        w.i32_and(); w.i32_or()
        w.i32_eqz(); w.br_if(blk)
        w.local_get(e); w.i32_const(1); w.i32_sub(); w.local_set(e)
        w.br(top)
        w.end(); w.end()
        w.local_get(p); w.local_get(s); w.i32_add()
        w.local_get(e); w.local_get(s); w.i32_sub()
        w.call(self.h["make_text"])

    def _b_replace(self):
        f = self._bh("replace"); w = f.w
        t = f.new_local(); a = f.new_local(); b = f.new_local()
        p = f.new_local(); l = f.new_local()
        pa = f.new_local(); la = f.new_local()
        pb = f.new_local(); lb = f.new_local()
        sb = f.new_local(); i = f.new_local(); j = f.new_local()
        w.local_get(1); w.call(self.h["to_text"]); w.local_set(t)
        w.local_get(2); w.call(self.h["to_text"]); w.local_set(a)
        w.local_get(3); w.call(self.h["to_text"]); w.local_set(b)
        w.local_get(t); w.i32_load(4); w.local_set(p)
        w.local_get(t); w.i32_load(8); w.local_set(l)
        w.local_get(a); w.i32_load(4); w.local_set(pa)
        w.local_get(a); w.i32_load(8); w.local_set(la)
        w.local_get(b); w.i32_load(4); w.local_set(pb)
        w.local_get(b); w.i32_load(8); w.local_set(lb)
        w.call(self.h["sb_new"]); w.local_set(sb)
        # empty needle: Python inserts between every byte
        w.local_get(la); w.i32_eqz()
        lz = w.if_()
        w.local_get(sb); w.local_get(pb); w.local_get(lb); w.call(self.h["sb_push"])
        w.i32_const(0); w.local_set(i)
        blk = w.block(); top = w.loop()
        w.local_get(i); w.local_get(l); w.i32_ge_u(); w.br_if(blk)
        w.local_get(sb); w.local_get(p); w.local_get(i); w.i32_add(); w.i32_const(1)
        w.call(self.h["sb_push"])
        w.local_get(sb); w.local_get(pb); w.local_get(lb); w.call(self.h["sb_push"])
        w.local_get(i); w.i32_const(1); w.i32_add(); w.local_set(i)
        w.br(top)
        w.end(); w.end()
        w.local_get(sb); w.call(self.h["sb_finish"]); w.return_()
        w.end()
        w.i32_const(0); w.local_set(i)
        blk = w.block(); top = w.loop()
        w.local_get(i); w.local_get(l); w.i32_ge_u(); w.br_if(blk)
        # match? check i+la <= l and memcmp
        w.local_get(i); w.local_get(la); w.i32_add(); w.local_get(l); w.i32_le_u()
        l = w.if_()
        w.i32_const(0); w.local_set(j)
        inner = w.block(); itop = w.loop()
        w.local_get(j); w.local_get(la); w.i32_ge_u(); w.br_if(inner)
        w.local_get(p); w.local_get(i); w.i32_add(); w.local_get(j); w.i32_add()
        w.i32_load8_u()
        w.local_get(pa); w.local_get(j); w.i32_add(); w.i32_load8_u()
        w.i32_ne()
        l2 = w.if_()
        w.i32_const(-1); w.local_set(j)  # mismatch marker
        w.br(inner)
        w.end()
        w.local_get(j); w.i32_const(1); w.i32_add(); w.local_set(j)
        w.br(itop)
        w.end(); w.end()
        # j == la means full match (j==-1 means mismatch)
        w.local_get(j); w.local_get(la); w.i32_eq()
        l2 = w.if_()
        w.local_get(sb); w.local_get(pb); w.local_get(lb); w.call(self.h["sb_push"])
        w.local_get(i); w.local_get(la); w.i32_add(); w.local_set(i)
        w.br(top)
        w.end()
        w.end()  # end if i+la<=l
        w.local_get(sb); w.local_get(p); w.local_get(i); w.i32_add(); w.i32_const(1)
        w.call(self.h["sb_push"])
        w.local_get(i); w.i32_const(1); w.i32_add(); w.local_set(i)
        w.br(top)
        w.end(); w.end()
        w.local_get(sb); w.call(self.h["sb_finish"])

    def _b_split(self):
        f = self._bh("split"); w = f.w
        t = f.new_local(); s = f.new_local()
        p = f.new_local(); l = f.new_local()
        ps = f.new_local(); ls = f.new_local()
        out = f.new_local(); start = f.new_local(); i = f.new_local(); j = f.new_local()
        w.local_get(1); w.call(self.h["to_text"]); w.local_set(t)
        w.local_get(2); w.call(self.h["to_text"]); w.local_set(s)
        w.local_get(t); w.i32_load(4); w.local_set(p)
        w.local_get(t); w.i32_load(8); w.local_set(l)
        w.local_get(s); w.i32_load(4); w.local_set(ps)
        w.local_get(s); w.i32_load(8); w.local_set(ls)
        w.local_get(ls); w.i32_eqz()
        lz = w.if_()
        self.panic_line_local(w, 0, "empty separator")
        w.end()
        w.call(self.h["list_new"]); w.local_set(out)
        w.i32_const(0); w.local_set(start)
        w.i32_const(0); w.local_set(i)
        blk = w.block(); top = w.loop()
        w.local_get(i); w.local_get(ls); w.i32_add(); w.local_get(l); w.i32_gt_u()
        w.br_if(blk)
        w.i32_const(0); w.local_set(j)
        inner = w.block(); itop = w.loop()
        w.local_get(j); w.local_get(ls); w.i32_ge_u(); w.br_if(inner)
        w.local_get(p); w.local_get(i); w.i32_add(); w.local_get(j); w.i32_add()
        w.i32_load8_u()
        w.local_get(ps); w.local_get(j); w.i32_add(); w.i32_load8_u()
        w.i32_ne()
        l2 = w.if_()
        w.i32_const(-1); w.local_set(j)
        w.br(inner)
        w.end()
        w.local_get(j); w.i32_const(1); w.i32_add(); w.local_set(j)
        w.br(itop)
        w.end(); w.end()
        w.local_get(j); w.local_get(ls); w.i32_eq()
        l2 = w.if_()
        w.local_get(out)
        w.local_get(p); w.local_get(start); w.i32_add()
        w.local_get(i); w.local_get(start); w.i32_sub()
        w.call(self.h["make_text"])
        w.call(self.h["list_push"]); w.drop()
        w.local_get(i); w.local_get(ls); w.i32_add(); w.local_set(i)
        w.local_get(i); w.local_set(start)
        w.br(top)
        w.end()
        w.local_get(i); w.i32_const(1); w.i32_add(); w.local_set(i)
        w.br(top)
        w.end(); w.end()
        w.local_get(out)
        w.local_get(p); w.local_get(start); w.i32_add()
        w.local_get(l); w.local_get(start); w.i32_sub()
        w.call(self.h["make_text"])
        w.call(self.h["list_push"])

    def _b_join(self):
        # join(l, sep) — uses py_str semantics like the VM
        f = self._bh("join"); w = f.w
        to = f.new_local(); n = f.new_local(); i = f.new_local()
        sb = f.new_local(); sep = f.new_local()
        p = f.new_local(); bl = f.new_local(); bo = f.new_local(); e = f.new_local()
        first = f.new_local()
        w.local_get(2); w.call(self.h["to_text"]); w.local_set(sep)
        w.local_get(1); w.i32_load(0); w.local_set(to)
        w.call(self.h["sb_new"]); w.local_set(sb)
        w.i32_const(1); w.local_set(first)
        # push_one(e): emit sep unless first, then py_str(e)
        # implemented inline at the two call sites via duplicated code is
        # wasteful; instead write a tiny local helper closure using locals.
        # WASM has no closures, so duplicate the ~10 instructions.
        w.local_get(to); w.i32_const(TAG_LIST); w.i32_eq()
        l = w.if_()
        w.local_get(1); w.i32_load(8); w.local_set(n)
        w.i32_const(0); w.local_set(i)
        blk = w.block(); top = w.loop()
        w.local_get(i); w.local_get(n); w.i32_ge_u(); w.br_if(blk)
        w.local_get(1); w.local_get(i); w.call(self.h["list_get0"]); w.local_set(e)
        w.local_get(first); w.i32_eqz()
        l2 = w.if_()
        w.local_get(sb); w.local_get(sep); w.call(self.h["sb_push_text"])
        w.end()
        w.i32_const(0); w.local_set(first)
        w.local_get(sb); w.local_get(e); w.i32_const(0)
        w.call(self.h["py_str"]); w.call(self.h["sb_push_text"])
        w.local_get(i); w.i32_const(1); w.i32_add(); w.local_set(i)
        w.br(top)
        w.end(); w.end()
        w.local_get(sb); w.call(self.h["sb_finish"]); w.return_()
        w.end()
        w.local_get(to); w.i32_const(TAG_TEXT); w.i32_eq()
        l = w.if_()
        w.local_get(1); w.i32_load(4); w.local_set(p)
        w.local_get(1); w.i32_load(8); w.local_set(bl)
        w.local_get(p); w.local_get(bl); w.call(self.h["utf8_len"]); w.local_set(n)
        w.i32_const(0); w.local_set(i)
        blk = w.block(); top = w.loop()
        w.local_get(i); w.local_get(n); w.i32_ge_u(); w.br_if(blk)
        w.local_get(p); w.local_get(bl); w.local_get(i)
        w.call(self.h["utf8_byte_offset"]); w.local_set(bo)
        w.local_get(p); w.local_get(bl); w.local_get(i); w.i32_const(1); w.i32_add()
        w.call(self.h["utf8_byte_offset"]); w.local_set(e)
        w.local_get(e); w.local_get(bo); w.i32_sub(); w.local_set(e)
        w.local_get(first); w.i32_eqz()
        l2 = w.if_()
        w.local_get(sb); w.local_get(sep); w.call(self.h["sb_push_text"])
        w.end()
        w.i32_const(0); w.local_set(first)
        w.local_get(sb); w.local_get(p); w.local_get(bo); w.i32_add(); w.local_get(e)
        w.call(self.h["sb_push"])
        w.local_get(i); w.i32_const(1); w.i32_add(); w.local_set(i)
        w.br(top)
        w.end(); w.end()
        w.local_get(sb); w.call(self.h["sb_finish"]); w.return_()
        w.end()
        self.panic_line_local(w, 0, "I can't join that value.")
        w.unreachable()

    def _b_has(self):
        f = self._bh("has"); w = f.w
        w.local_get(0); w.local_get(2); w.local_get(1)
        w.call(self.h["in_op"])
        w.call(self.h["make_yesno"])

    def _b_sum_avg(self):
        for name, is_avg in (("sum", False), ("average", True)):
            f = self._bh(name); w = f.w
            n = f.new_local(); i = f.new_local()
            e = f.new_local(); tot = f.new_local(F64)
            w.local_get(1); w.i32_load(0); w.i32_const(TAG_LIST); w.i32_ne()
            l = w.if_()
            self.panic_line_local(w, 0, "I need a list for that.")
            w.end()
            w.local_get(1); w.i32_load(8); w.local_set(n)
            w.f64_const(0.0); w.local_set(tot)
            w.i32_const(0); w.local_set(i)
            blk = w.block(); top = w.loop()
            w.local_get(i); w.local_get(n); w.i32_ge_u(); w.br_if(blk)
            w.local_get(1); w.local_get(i); w.call(self.h["list_get0"]); w.local_set(e)
            w.local_get(e); w.i32_load(0); w.i32_const(TAG_NUMBER); w.i32_ne()
            l2 = w.if_()
            self.panic_line_local(w, 0, "I can only total up numbers.")
            w.end()
            w.local_get(tot); w.local_get(e); w.f64_load(4); w.f64_add()
            w.local_set(tot)
            w.local_get(i); w.i32_const(1); w.i32_add(); w.local_set(i)
            w.br(top)
            w.end(); w.end()
            if is_avg:
                w.local_get(n); w.i32_eqz()
                l = w.if_()
                self.panic_line_local(w, 0, "division by zero")
                w.end()
                w.local_get(tot); w.local_get(n); w.f64_convert_i32_s(); w.f64_div()
            else:
                w.local_get(tot)
            w.call(self.h["make_number"])

    def _b_math1(self):
        ops = [("abs", "f64_abs"), ("ceil", "f64_ceil"), ("floor", "f64_floor"),
               ("round", "f64_nearest"), ("sqrt", "f64_sqrt")]
        for name, insn in ops:
            f = self._bh(name); w = f.w
            fv = f.new_local(F64)
            w.local_get(1); w.i32_load(0); w.i32_const(TAG_NUMBER); w.i32_ne()
            l = w.if_()
            self.panic_line_local(w, 0, "I need a number for that.")
            w.end()
            w.local_get(1); w.f64_load(4); w.local_set(fv)
            if name == "sqrt":
                w.local_get(fv); w.f64_const(0.0); w.f64_lt()
                l = w.if_()
                self.panic_line_local(w, 0, "math domain error")
                w.end()
            w.local_get(fv)
            getattr(w, insn)()
            w.call(self.h["make_number"])

    def _b_max_min(self):
        for name, want_max in (("max", True), ("min", False)):
            f = self._bh(name); w = f.w
            items = f.new_local(); n = f.new_local(); i = f.new_local()
            e = f.new_local(); best = f.new_local()
            bf = f.new_local(F64); ef = f.new_local(F64); mode = f.new_local()
            w.local_get(1); w.local_set(items)
            w.local_get(1); w.i32_load(8); w.local_set(n)
            # single list arg unwraps
            w.local_get(n); w.i32_const(1); w.i32_eq()
            l = w.if_()
            w.local_get(1); w.i32_const(0); w.call(self.h["list_get0"])
            w.i32_load(0); w.i32_const(TAG_LIST); w.i32_eq()
            l2 = w.if_()
            w.local_get(1); w.i32_const(0); w.call(self.h["list_get0"])
            w.local_set(items)
            w.local_get(items); w.i32_load(8); w.local_set(n)
            w.end()
            w.end()
            w.local_get(n); w.i32_eqz()
            l = w.if_()
            self.panic_line_local(w, 0, name + "() arg is an empty sequence")
            w.end()
            w.local_get(items); w.i32_const(0); w.call(self.h["list_get0"])
            w.local_set(best)
            w.local_get(best); w.i32_load(0); w.i32_const(TAG_NUMBER); w.i32_eq()
            l = w.if_()
            w.i32_const(0); w.local_set(mode)  # numbers
            w.local_get(best); w.f64_load(4); w.local_set(bf)
            w.end()
            w.local_get(best); w.i32_load(0); w.i32_const(TAG_TEXT); w.i32_eq()
            l = w.if_()
            w.i32_const(1); w.local_set(mode)  # text
            w.end()
            w.local_get(mode); w.i32_const(0); w.i32_ne()
            w.local_get(mode); w.i32_const(1); w.i32_ne()
            w.i32_and()
            l = w.if_()
            self.panic_line_local(w, 0, "I can't compare those values.")
            w.end()
            w.i32_const(1); w.local_set(i)
            blk = w.block(); top = w.loop()
            w.local_get(i); w.local_get(n); w.i32_ge_u(); w.br_if(blk)
            w.local_get(items); w.local_get(i); w.call(self.h["list_get0"]); w.local_set(e)
            w.local_get(mode); w.i32_eqz()  # numbers
            l = w.if_()
            w.local_get(e); w.i32_load(0); w.i32_const(TAG_NUMBER); w.i32_ne()
            l2 = w.if_()
            self.panic_line_local(w, 0, "I can't compare those values.")
            w.end()
            w.local_get(e); w.f64_load(4); w.local_set(ef)
            # better = (ef > bf) == want_max
            w.local_get(ef); w.local_get(bf); w.f64_gt()
            w.i32_const(1 if want_max else 0); w.i32_eq()
            l2 = w.if_()
            w.local_get(e); w.local_set(best)
            w.local_get(ef); w.local_set(bf)
            w.end()
            w.end()
            # text mode
            w.local_get(mode); w.i32_const(1); w.i32_eq()
            l = w.if_()
            w.local_get(e); w.i32_load(0); w.i32_const(TAG_TEXT); w.i32_ne()
            l2 = w.if_()
            self.panic_line_local(w, 0, "I can't compare those values.")
            w.end()
            w.local_get(e); w.local_get(best); w.call(self.h["text_cmp"])
            w.i32_const(0); w.i32_gt_s()
            w.i32_const(1 if want_max else 0); w.i32_eq()
            l2 = w.if_()
            w.local_get(e); w.local_set(best)
            w.end()
            w.end()
            w.local_get(i); w.i32_const(1); w.i32_add(); w.local_set(i)
            w.br(top)
            w.end(); w.end()
            w.local_get(best)

    def _b_sorted(self):
        f = self._bh("sorted"); w = f.w
        out = f.new_local(); n = f.new_local(); i = f.new_local(); j = f.new_local()
        v = f.new_local(); u = f.new_local(); mode = f.new_local()
        ef = f.new_local(F64); uf = f.new_local(F64)
        should_move = f.new_local()
        w.local_get(1); w.i32_load(0); w.i32_const(TAG_LIST); w.i32_ne()
        l = w.if_()
        self.panic_line_local(w, 0, "I can only sort a list.")
        w.end()
        w.local_get(1); w.i32_load(8); w.local_set(n)
        w.call(self.h["list_new"]); w.local_set(out)
        w.i32_const(0); w.local_set(i)
        blk = w.block(); top = w.loop()
        w.local_get(i); w.local_get(n); w.i32_ge_u(); w.br_if(blk)
        w.local_get(out); w.local_get(1); w.local_get(i)
        w.call(self.h["list_get0"]); w.call(self.h["list_push"]); w.drop()
        w.local_get(i); w.i32_const(1); w.i32_add(); w.local_set(i)
        w.br(top)
        w.end(); w.end()
        # detect mode from first element
        w.local_get(n); w.i32_eqz()
        l = w.if_()
        w.local_get(out); w.return_()
        w.end()
        w.i32_const(-1); w.local_set(mode)
        w.local_get(out); w.i32_const(0); w.call(self.h["list_get0"])
        w.i32_load(0); w.i32_const(TAG_NUMBER); w.i32_eq()
        l = w.if_()
        w.i32_const(0); w.local_set(mode)
        w.end()
        w.local_get(out); w.i32_const(0); w.call(self.h["list_get0"])
        w.i32_load(0); w.i32_const(TAG_TEXT); w.i32_eq()
        l = w.if_()
        w.i32_const(1); w.local_set(mode)
        w.end()
        w.local_get(mode); w.i32_const(0); w.i32_ne()
        w.local_get(mode); w.i32_const(1); w.i32_ne()
        w.i32_and()
        l = w.if_()
        self.panic_line_local(w, 0, "I can't sort those values.")
        w.end()
        # insertion sort; compare via mode
        w.i32_const(1); w.local_set(i)
        outer = w.block(); otop = w.loop()
        w.local_get(i); w.local_get(n); w.i32_ge_u(); w.br_if(outer)
        w.local_get(out); w.local_get(i); w.call(self.h["list_get0"]); w.local_set(v)
        w.local_get(i); w.i32_const(1); w.i32_sub(); w.local_set(j)
        inner = w.block(); itop = w.loop()
        w.local_get(j); w.i32_const(0); w.i32_lt_s(); w.br_if(inner)
        w.local_get(out); w.local_get(j); w.call(self.h["list_get0"]); w.local_set(u)
        # should u move right? u > v ?
        w.local_get(mode); w.i32_eqz()
        l = w.if_()
        w.local_get(u); w.f64_load(4); w.local_set(uf)
        w.local_get(v); w.f64_load(4); w.local_set(ef)
        w.local_get(uf); w.local_get(ef); w.f64_gt()
        w.local_set(should_move)
        w.else_()
        w.local_get(u); w.local_get(v); w.call(self.h["text_cmp"])
        w.i32_const(0); w.i32_gt_s()
        w.local_set(should_move)
        w.end()
        w.local_get(should_move)
        w.i32_eqz(); w.br_if(inner)
        w.local_get(out); w.i32_load(4)
        w.local_get(j); w.i32_const(1); w.i32_add(); w.i32_const(2); w.i32_shl(); w.i32_add()
        w.local_get(u); w.i32_store(0)
        w.local_get(j); w.i32_const(1); w.i32_sub(); w.local_set(j)
        w.br(itop)
        w.end(); w.end()
        w.local_get(out); w.i32_load(4)
        w.local_get(j); w.i32_const(1); w.i32_add(); w.i32_const(2); w.i32_shl(); w.i32_add()
        w.local_get(v); w.i32_store(0)
        w.local_get(i); w.i32_const(1); w.i32_add(); w.local_set(i)
        w.br(otop)
        w.end(); w.end()
        w.local_get(out)

    def _b_reversed(self):
        f = self._bh("reversed"); w = f.w
        to = f.new_local(); n = f.new_local(); i = f.new_local()
        out = f.new_local(); p = f.new_local(); bl = f.new_local()
        bo = f.new_local(); e = f.new_local()
        w.local_get(1); w.i32_load(0); w.local_set(to)
        w.local_get(to); w.i32_const(TAG_LIST); w.i32_eq()
        l = w.if_()
        w.local_get(1); w.i32_load(8); w.local_set(n)
        w.call(self.h["list_new"]); w.local_set(out)
        w.local_get(n); w.i32_const(1); w.i32_sub(); w.local_set(i)
        blk = w.block(); top = w.loop()
        w.local_get(i); w.i32_const(0); w.i32_lt_s(); w.br_if(blk)
        w.local_get(out); w.local_get(1); w.local_get(i)
        w.call(self.h["list_get0"]); w.call(self.h["list_push"]); w.drop()
        w.local_get(i); w.i32_const(1); w.i32_sub(); w.local_set(i)
        w.br(top)
        w.end(); w.end()
        w.local_get(out); w.return_()
        w.end()
        w.local_get(to); w.i32_const(TAG_TEXT); w.i32_eq()
        l = w.if_()
        w.local_get(1); w.i32_load(4); w.local_set(p)
        w.local_get(1); w.i32_load(8); w.local_set(bl)
        w.local_get(p); w.local_get(bl); w.call(self.h["utf8_len"]); w.local_set(n)
        w.call(self.h["list_new"]); w.local_set(out)
        w.local_get(n); w.i32_const(1); w.i32_sub(); w.local_set(i)
        blk = w.block(); top = w.loop()
        w.local_get(i); w.i32_const(0); w.i32_lt_s(); w.br_if(blk)
        w.local_get(p); w.local_get(bl); w.local_get(i)
        w.call(self.h["utf8_byte_offset"]); w.local_set(bo)
        w.local_get(p); w.local_get(bl); w.local_get(i); w.i32_const(1); w.i32_add()
        w.call(self.h["utf8_byte_offset"]); w.local_set(e)
        w.local_get(e); w.local_get(bo); w.i32_sub(); w.local_set(e)
        w.local_get(out)
        w.local_get(p); w.local_get(bo); w.i32_add(); w.local_get(e)
        w.call(self.h["make_text"])
        w.call(self.h["list_push"]); w.drop()
        w.local_get(i); w.i32_const(1); w.i32_sub(); w.local_set(i)
        w.br(top)
        w.end(); w.end()
        w.local_get(out); w.return_()
        w.end()
        self.panic_line_local(w, 0, "I can't reverse that value.")
        w.unreachable()

    def _b_unique(self):
        f = self._bh("unique"); w = f.w
        to = f.new_local(); n = f.new_local(); i = f.new_local(); j = f.new_local()
        out = f.new_local(); p = f.new_local(); bl = f.new_local()
        bo = f.new_local(); e = f.new_local(); u = f.new_local(); m = f.new_local()
        w.local_get(1); w.i32_load(0); w.local_set(to)
        w.local_get(to); w.i32_const(TAG_LIST); w.i32_eq()
        w.local_get(to); w.i32_const(TAG_TEXT); w.i32_eq()
        w.i32_or(); w.i32_eqz()
        l = w.if_()
        self.panic_line_local(w, 0, "I can only pick unique values from a list or text.")
        w.end()
        # build source list of elements
        w.call(self.h["list_new"]); w.local_set(out)
        w.local_get(to); w.i32_const(TAG_LIST); w.i32_eq()
        l = w.if_()
        w.local_get(1); w.i32_load(8); w.local_set(n)
        w.i32_const(0); w.local_set(i)
        w.end()
        w.local_get(to); w.i32_const(TAG_TEXT); w.i32_eq()
        l = w.if_()
        w.local_get(1); w.i32_load(4); w.local_set(p)
        w.local_get(1); w.i32_load(8); w.local_set(bl)
        w.local_get(p); w.local_get(bl); w.call(self.h["utf8_len"]); w.local_set(n)
        w.i32_const(1); w.local_set(to)  # reuse to as text-flag
        w.i32_const(0); w.local_set(i)
        w.end()
        # iterate source elements into e
        blk = w.block(); top = w.loop()
        w.local_get(i); w.local_get(n); w.i32_ge_u(); w.br_if(blk)
        w.local_get(to); w.i32_const(1); w.i32_eq()
        l = w.if_()  # text source
        w.local_get(p); w.local_get(bl); w.local_get(i)
        w.call(self.h["utf8_byte_offset"]); w.local_set(bo)
        w.local_get(p); w.local_get(bl); w.local_get(i); w.i32_const(1); w.i32_add()
        w.call(self.h["utf8_byte_offset"]); w.local_set(e)
        w.local_get(e); w.local_get(bo); w.i32_sub(); w.local_set(e)
        w.local_get(p); w.local_get(bo); w.i32_add(); w.local_get(e)
        w.call(self.h["make_text"]); w.local_set(e)
        w.end()
        w.local_get(to); w.i32_const(1); w.i32_ne()
        l = w.if_()  # list source
        w.local_get(1); w.local_get(i); w.call(self.h["list_get0"]); w.local_set(e)
        w.end()
        # membership check
        w.i32_const(0); w.local_set(m)
        w.local_get(out); w.i32_load(8); w.local_set(j)
        w.i32_const(0); w.local_set(bo)  # reuse bo as k
        inner = w.block(); itop = w.loop()
        w.local_get(bo); w.local_get(j); w.i32_ge_u(); w.br_if(inner)
        w.local_get(out); w.local_get(bo); w.call(self.h["list_get0"]); w.local_set(u)
        w.local_get(e); w.local_get(u); w.call(self.h["equals"])
        l2 = w.if_()
        w.i32_const(1); w.local_set(m)
        w.br(inner)
        w.end()
        w.local_get(bo); w.i32_const(1); w.i32_add(); w.local_set(bo)
        w.br(itop)
        w.end(); w.end()
        w.local_get(m); w.i32_eqz()
        l2 = w.if_()
        w.local_get(out); w.local_get(e); w.call(self.h["list_push"]); w.drop()
        w.end()
        w.local_get(i); w.i32_const(1); w.i32_add(); w.local_set(i)
        w.br(top)
        w.end(); w.end()
        w.local_get(out)

    def _b_keys(self):
        f = self._bh("keys"); w = f.w
        n = f.new_local(); i = f.new_local(); out = f.new_local()
        w.local_get(1); w.i32_load(0); w.i32_const(TAG_RECORD); w.i32_ne()
        l = w.if_()
        self.panic_line_local(w, 0, "I need a record for that.")
        w.end()
        w.local_get(1); w.i32_load(8); w.local_set(n)
        w.call(self.h["list_new"]); w.local_set(out)
        w.i32_const(0); w.local_set(i)
        blk = w.block(); top = w.loop()
        w.local_get(i); w.local_get(n); w.i32_ge_u(); w.br_if(blk)
        w.local_get(out)
        w.local_get(1); w.i32_load(4)
        w.local_get(i); w.i32_const(3); w.i32_shl(); w.i32_add()
        w.i32_load(0)
        w.call(self.h["list_push"]); w.drop()
        w.local_get(i); w.i32_const(1); w.i32_add(); w.local_set(i)
        w.br(top)
        w.end(); w.end()
        w.local_get(out)

    def _b_starts_ends(self):
        for name, is_start in (("starts_with", True), ("ends_with", False)):
            f = self._bh(name); w = f.w
            t = f.new_local(); q = f.new_local()
            p = f.new_local(); l = f.new_local()
            pq = f.new_local(); lq = f.new_local()
            i = f.new_local()
            w.local_get(1); w.call(self.h["to_text"]); w.local_set(t)
            w.local_get(2); w.call(self.h["to_text"]); w.local_set(q)
            w.local_get(t); w.i32_load(4); w.local_set(p)
            w.local_get(t); w.i32_load(8); w.local_set(l)
            w.local_get(q); w.i32_load(4); w.local_set(pq)
            w.local_get(q); w.i32_load(8); w.local_set(lq)
            w.local_get(lq); w.local_get(l); w.i32_gt_u()
            lz = w.if_()
            w.i32_const(0); w.call(self.h["make_yesno"]); w.return_()
            w.end()
            if not is_start:
                w.local_get(p); w.local_get(l); w.local_get(lq); w.i32_sub()
                w.i32_add(); w.local_set(p)
            w.i32_const(0); w.local_set(i)
            blk = w.block(); top = w.loop()
            w.local_get(i); w.local_get(lq); w.i32_ge_u(); w.br_if(blk)
            w.local_get(p); w.local_get(i); w.i32_add(); w.i32_load8_u()
            w.local_get(pq); w.local_get(i); w.i32_add(); w.i32_load8_u()
            w.i32_ne()
            l2 = w.if_()
            w.i32_const(0); w.call(self.h["make_yesno"]); w.return_()
            w.end()
            w.local_get(i); w.i32_const(1); w.i32_add(); w.local_set(i)
            w.br(top)
            w.end(); w.end()
            w.i32_const(1); w.call(self.h["make_yesno"])

    def _b_count_of(self):
        f = self._bh("count_of"); w = f.w
        tc = f.new_local(); tx = f.new_local()
        c = f.new_local(); x = f.new_local()
        p = f.new_local(); l = f.new_local()
        px = f.new_local(); lx = f.new_local()
        i = f.new_local(); j = f.new_local(); cnt = f.new_local()
        w.local_get(1); w.i32_load(0); w.local_set(tc)
        w.local_get(2); w.i32_load(0); w.local_set(tx)
        w.local_get(tc); w.i32_const(TAG_TEXT); w.i32_eq()
        w.local_get(tx); w.i32_const(TAG_TEXT); w.i32_eq()
        w.i32_and()
        l = w.if_()
        w.local_get(1); w.call(self.h["to_text"]); w.local_set(c)
        w.local_get(2); w.call(self.h["to_text"]); w.local_set(x)
        w.local_get(c); w.i32_load(4); w.local_set(p)
        w.local_get(c); w.i32_load(8); w.local_set(l)
        w.local_get(x); w.i32_load(4); w.local_set(px)
        w.local_get(x); w.i32_load(8); w.local_set(lx)
        # empty needle: Python counts len+1 (char count)
        w.local_get(lx); w.i32_eqz()
        lz = w.if_()
        w.local_get(p); w.local_get(l); w.call(self.h["utf8_len"])
        w.i32_const(1); w.i32_add()
        w.f64_convert_i32_s()
        w.call(self.h["make_number"]); w.return_()
        w.end()
        w.i32_const(0); w.local_set(cnt)
        w.i32_const(0); w.local_set(i)
        blk = w.block(); top = w.loop()
        w.local_get(i); w.local_get(lx); w.i32_add(); w.local_get(l); w.i32_gt_u()
        w.br_if(blk)
        w.i32_const(0); w.local_set(j)
        inner = w.block(); itop = w.loop()
        w.local_get(j); w.local_get(lx); w.i32_ge_u(); w.br_if(inner)
        w.local_get(p); w.local_get(i); w.i32_add(); w.local_get(j); w.i32_add()
        w.i32_load8_u()
        w.local_get(px); w.local_get(j); w.i32_add(); w.i32_load8_u()
        w.i32_ne()
        l2 = w.if_()
        w.i32_const(-1); w.local_set(j)
        w.br(inner)
        w.end()
        w.local_get(j); w.i32_const(1); w.i32_add(); w.local_set(j)
        w.br(itop)
        w.end(); w.end()
        w.local_get(j); w.local_get(lx); w.i32_eq()
        l2 = w.if_()
        w.local_get(cnt); w.i32_const(1); w.i32_add(); w.local_set(cnt)
        w.local_get(i); w.local_get(lx); w.i32_add(); w.local_set(i)
        w.br(top)
        w.end()
        w.local_get(i); w.i32_const(1); w.i32_add(); w.local_set(i)
        w.br(top)
        w.end(); w.end()
        w.local_get(cnt); w.f64_convert_i32_s(); w.call(self.h["make_number"])
        w.return_()
        w.end()
        # list needle
        w.local_get(tx); w.i32_const(TAG_LIST); w.i32_eq()
        l = w.if_()
        w.local_get(2); w.i32_load(8); w.local_set(l)
        w.i32_const(0); w.local_set(cnt)
        w.i32_const(0); w.local_set(i)
        blk = w.block(); top = w.loop()
        w.local_get(i); w.local_get(l); w.i32_ge_u(); w.br_if(blk)
        w.local_get(1)
        w.local_get(2); w.local_get(i); w.call(self.h["list_get0"])
        w.call(self.h["equals"])
        l2 = w.if_()
        w.local_get(cnt); w.i32_const(1); w.i32_add(); w.local_set(cnt)
        w.end()
        w.local_get(i); w.i32_const(1); w.i32_add(); w.local_set(i)
        w.br(top)
        w.end(); w.end()
        w.local_get(cnt); w.f64_convert_i32_s(); w.call(self.h["make_number"])
        w.return_()
        w.end()
        self.panic_line_local(w, 0, "I can't count those.")
        w.unreachable()

    def _b_range(self):
        f = self._bh("niko_range"); w = f.w
        a = f.new_local(); b = f.new_local(); out = f.new_local()
        w.local_get(0); w.local_get(1); w.call(self.h["to_int"]); w.local_set(a)
        w.local_get(0); w.local_get(2); w.call(self.h["to_int"]); w.local_set(b)
        w.call(self.h["list_new"]); w.local_set(out)
        w.local_get(a); w.local_get(b); w.i32_le_s()
        l = w.if_()
        blk = w.block(); top = w.loop()
        w.local_get(a); w.local_get(b); w.i32_gt_s(); w.br_if(blk)
        w.local_get(out); w.local_get(a); w.f64_convert_i32_s()
        w.call(self.h["make_number"]); w.call(self.h["list_push"]); w.drop()
        w.local_get(a); w.i32_const(1); w.i32_add(); w.local_set(a)
        w.br(top)
        w.end(); w.end()
        w.local_get(out); w.return_()
        w.end()
        blk = w.block(); top = w.loop()
        w.local_get(a); w.local_get(b); w.i32_lt_s(); w.br_if(blk)
        w.local_get(out); w.local_get(a); w.f64_convert_i32_s()
        w.call(self.h["make_number"]); w.call(self.h["list_push"]); w.drop()
        w.local_get(a); w.i32_const(1); w.i32_sub(); w.local_set(a)
        w.br(top)
        w.end(); w.end()
        w.local_get(out)

    def _b_results(self):
        # ok(v)
        f = self._bh("ok"); w = f.w
        w.local_get(1); w.i32_const(1); w.call(self.h["make_result"])
        # error(m): message uses fmt() like the VM
        f = self._bh("error"); w = f.w
        m = f.new_local()
        w.local_get(1); w.call(self.h["to_text"]); w.local_set(m)
        w.local_get(m); w.i32_const(0); w.call(self.h["make_result"])
        # is_ok / is_error: isinstance-style, False for non-results (no panic)
        for name, want in (("is_ok", 1), ("is_error", 0)):
            f = self._bh(name); w = f.w
            w.local_get(1); w.i32_load(0); w.i32_const(TAG_RESULT); w.i32_eq()
            w.local_get(1); w.i32_load(4); w.i32_const(want); w.i32_eq()
            w.i32_and()
            w.call(self.h["make_yesno"])
        # unwrap(r): nothing -> panic; non-result -> return as-is; error -> panic(msg)
        f = self._bh("unwrap"); w = f.w
        t = f.new_local()
        w.local_get(1); w.i32_eqz()
        l = w.if_()
        self.emit_panic(w, "tried to unwrap nothing")
        w.end()
        w.local_get(1); w.i32_load(0); w.i32_const(TAG_RESULT); w.i32_ne()
        l = w.if_()
        w.local_get(1); w.return_()
        w.end()
        w.local_get(1); w.i32_load(4)
        l = w.if_()
        w.local_get(1); w.i32_load(8); w.return_()
        w.end()
        w.local_get(1); w.i32_load(8); w.local_set(t)
        w.local_get(t); w.i32_load(4)
        w.local_get(t); w.i32_load(8)
        w.call(self.imp["panic"]); w.unreachable()
        # unwrap_or(r, d): result -> value or d; nothing -> d; else r
        f = self._bh("unwrap_or"); w = f.w
        w.local_get(1); w.i32_eqz()
        l = w.if_()
        w.local_get(2); w.return_()
        w.end()
        w.local_get(1); w.i32_load(0); w.i32_const(TAG_RESULT); w.i32_ne()
        l = w.if_()
        w.local_get(1); w.return_()
        w.end()
        w.local_get(1); w.i32_load(4)
        l = w.if_()
        w.local_get(1); w.i32_load(8); w.return_()
        w.end()
        w.local_get(2)
        # error_message(r): message of error results, else ""
        f = self._bh("error_message"); w = f.w
        w.local_get(1); w.i32_load(0); w.i32_const(TAG_RESULT); w.i32_eq()
        w.local_get(1); w.i32_load(4); w.i32_eqz()
        w.i32_and()
        l = w.if_()
        w.local_get(1); w.i32_load(8); w.return_()
        w.end()
        w.i32_const(self.text_val(""))
        # try_number(x): mirrors number(x); failures become error results
        f = self._bh("try_number"); w = f.w
        tag = f.new_local(); s = f.new_local(); fv = f.new_local(F64)
        w.local_get(1); w.i32_load(0); w.local_set(tag)
        w.local_get(tag); w.i32_const(TAG_NUMBER); w.i32_eq()
        l = w.if_()
        w.local_get(0); w.local_get(1); w.call(self.h["to_int"])
        w.f64_convert_i32_s(); w.call(self.h["make_number"])
        w.i32_const(1); w.call(self.h["make_result"]); w.return_()
        w.end()
        w.local_get(tag); w.i32_const(TAG_YESNO); w.i32_eq()
        l = w.if_()
        w.local_get(1); w.i32_load(4); w.f64_convert_i32_s()
        w.call(self.h["make_number"])
        w.i32_const(1); w.call(self.h["make_result"]); w.return_()
        w.end()
        w.local_get(tag); w.i32_const(TAG_TEXT); w.i32_eq()
        l = w.if_()
        w.local_get(1); w.call(self.h["to_text"]); w.local_set(s)
        w.local_get(s); w.i32_load(4); w.local_get(s); w.i32_load(8)
        w.call(self.imp["parse_num"]); w.local_set(fv)
        w.local_get(fv); w.local_get(fv); w.f64_ne()
        l2 = w.if_()
        w.local_get(s); w.i32_const(0); w.call(self.h["make_result"]); w.return_()
        w.end()
        w.local_get(fv); w.call(self.h["make_number"])
        w.i32_const(1); w.call(self.h["make_result"]); w.return_()
        w.end()
        # anything else: error("I can't turn <repr> into a number.")
        w.local_get(1); w.i32_const(0); w.call(self.h["py_str"]); w.local_set(s)
        w.i32_const(self.text_val("I can't turn "))
        w.local_get(s)
        w.i32_const(self.text_val(" into a number."))
        w.call(self.h["concat3"])
        w.i32_const(0); w.call(self.h["make_result"])

    def _b_random_pick(self):
        # random_int(a, b): inclusive; mirrors Python's exact empty-range message
        f = self._bh("random_int"); w = f.w
        a = f.new_local(); b = f.new_local()
        w.local_get(0); w.local_get(1); w.call(self.h["to_int"]); w.local_set(a)
        w.local_get(0); w.local_get(2); w.call(self.h["to_int"]); w.local_set(b)
        w.local_get(a); w.local_get(b); w.i32_gt_s()
        l = w.if_()
        self.panic_line_local(w, 0, "empty range for randrange()")
        w.end()
        w.local_get(a); w.local_get(b); w.call(self.imp["random_i32"])
        w.f64_convert_i32_s(); w.call(self.h["make_number"])
        # pick(x): mirrors the VM's direct "I can't pick from an empty list."
        f = self._bh("pick"); w = f.w
        to = f.new_local(); n = f.new_local(); i = f.new_local()
        p = f.new_local(); bl = f.new_local(); bo = f.new_local(); e = f.new_local()
        w.local_get(1); w.i32_load(0); w.local_set(to)
        w.local_get(to); w.i32_const(TAG_LIST); w.i32_eq()
        l = w.if_()
        w.local_get(1); w.i32_load(8); w.local_set(n)
        w.local_get(n); w.i32_eqz()
        l2 = w.if_()
        self.emit_panic(w, "I can't pick from an empty list.")
        w.end()
        w.i32_const(0); w.local_get(n); w.i32_const(1); w.i32_sub()
        w.call(self.imp["random_i32"]); w.local_set(i)
        w.local_get(1); w.local_get(i); w.call(self.h["list_get0"]); w.return_()
        w.end()
        w.local_get(to); w.i32_const(TAG_TEXT); w.i32_eq()
        l = w.if_()
        w.local_get(1); w.i32_load(4); w.local_set(p)
        w.local_get(1); w.i32_load(8); w.local_set(bl)
        w.local_get(p); w.local_get(bl); w.call(self.h["utf8_len"]); w.local_set(n)
        w.local_get(n); w.i32_eqz()
        l2 = w.if_()
        self.emit_panic(w, "I can't pick from an empty list.")
        w.end()
        w.i32_const(0); w.local_get(n); w.i32_const(1); w.i32_sub()
        w.call(self.imp["random_i32"]); w.local_set(i)
        w.local_get(p); w.local_get(bl); w.local_get(i)
        w.call(self.h["utf8_byte_offset"]); w.local_set(bo)
        w.local_get(p); w.local_get(bl); w.local_get(i); w.i32_const(1); w.i32_add()
        w.call(self.h["utf8_byte_offset"]); w.local_set(e)
        w.local_get(e); w.local_get(bo); w.i32_sub(); w.local_set(e)
        w.local_get(p); w.local_get(bo); w.i32_add(); w.local_get(e)
        w.call(self.h["make_text"]); w.return_()
        w.end()
        self.panic_line_local(w, 0, "I can't pick from that value.")
        w.unreachable()

    def _b_time(self):
        # today() / now(): host returns (ptr, len)
        for name in ("today", "now"):
            f = self._bh(name); w = f.w
            p = f.new_local(); ln = f.new_local()
            w.call(self.imp[name])      # [ptr, len]
            w.local_set(ln)
            w.local_set(p)
            w.local_get(p); w.local_get(ln); w.call(self.h["make_text"])
        # sleep(x): VM returns None; line-checked number
        f = self._bh("sleep"); w = f.w
        w.local_get(1); w.i32_load(0); w.i32_const(TAG_NUMBER); w.i32_ne()
        l = w.if_()
        self.panic_line_local(w, 0, "I need a number for that.")
        w.end()
        w.local_get(1); w.f64_load(4); w.call(self.imp["sleep"])
        w.i32_const(0)  # nothing

    def _h_list_misc(self):
        # list_remove(l, v): remove the first element equal to v (no-op if absent)
        f = self._helper("list_remove", [I32, I32], [])
        w = f.w
        n = f.new_local(); i = f.new_local(); base = f.new_local(); j = f.new_local()
        w.local_get(0); w.i32_load(8); w.local_set(n)
        w.local_get(0); w.i32_load(4); w.local_set(base)
        w.i32_const(0); w.local_set(i)
        blk = w.block(); top = w.loop()
        w.local_get(i); w.local_get(n); w.i32_ge_u(); w.br_if(blk)
        w.local_get(base); w.local_get(i); w.i32_const(2); w.i32_shl(); w.i32_add()
        w.i32_load(0)
        w.local_get(1); w.call(self.h["equals"])
        l = w.if_()
        w.local_get(i); w.local_set(j)
        inner = w.block(); itop = w.loop()
        w.local_get(j); w.local_get(n); w.i32_const(1); w.i32_sub(); w.i32_ge_u()
        w.br_if(inner)
        w.local_get(base); w.local_get(j); w.i32_const(2); w.i32_shl(); w.i32_add()
        w.local_get(base); w.local_get(j); w.i32_const(1); w.i32_add()
        w.i32_const(2); w.i32_shl(); w.i32_add(); w.i32_load(0)
        w.i32_store(0)
        w.local_get(j); w.i32_const(1); w.i32_add(); w.local_set(j)
        w.br(itop)
        w.end(); w.end()
        w.local_get(0)
        w.local_get(n); w.i32_const(1); w.i32_sub(); w.i32_store(8)
        w.return_()
        w.end()
        w.local_get(i); w.i32_const(1); w.i32_add(); w.local_set(i)
        w.br(top)
        w.end(); w.end()

        # put_in(line, l, v): the `put v in l` statement (line unused; VM message has none)
        f = self._helper("put_in", [I32, I32, I32], [])
        w = f.w
        w.local_get(1); w.i32_load(0); w.i32_const(TAG_LIST); w.i32_ne()
        l = w.if_()
        self.emit_panic(w, '"put ... in" needs a list.')
        w.end()
        w.local_get(1); w.local_get(2); w.call(self.h["list_push"]); w.drop()

        # to_iter_list(line, v): normalize list/text/record for `for each`
        f = self._helper("to_iter_list", [I32, I32], [I32])
        w = f.w
        tag = f.new_local(); n = f.new_local(); i = f.new_local()
        out = f.new_local(); p = f.new_local(); bl = f.new_local()
        bo = f.new_local(); e = f.new_local()
        w.local_get(1); w.i32_load(0); w.local_set(tag)
        w.local_get(tag); w.i32_const(TAG_LIST); w.i32_eq()
        l = w.if_()
        w.local_get(1); w.return_()
        w.end()
        w.call(self.h["list_new"]); w.local_set(out)
        w.local_get(tag); w.i32_const(TAG_TEXT); w.i32_eq()
        l = w.if_()
        w.local_get(1); w.i32_load(4); w.local_set(p)
        w.local_get(1); w.i32_load(8); w.local_set(bl)
        w.local_get(p); w.local_get(bl); w.call(self.h["utf8_len"]); w.local_set(n)
        w.i32_const(0); w.local_set(i)
        blk = w.block(); top = w.loop()
        w.local_get(i); w.local_get(n); w.i32_ge_u(); w.br_if(blk)
        w.local_get(p); w.local_get(bl); w.local_get(i)
        w.call(self.h["utf8_byte_offset"]); w.local_set(bo)
        w.local_get(p); w.local_get(bl); w.local_get(i); w.i32_const(1); w.i32_add()
        w.call(self.h["utf8_byte_offset"]); w.local_set(e)
        w.local_get(e); w.local_get(bo); w.i32_sub(); w.local_set(e)
        w.local_get(out)
        w.local_get(p); w.local_get(bo); w.i32_add(); w.local_get(e)
        w.call(self.h["make_text"])
        w.call(self.h["list_push"]); w.drop()
        w.local_get(i); w.i32_const(1); w.i32_add(); w.local_set(i)
        w.br(top)
        w.end(); w.end()
        w.local_get(out); w.return_()
        w.end()
        w.local_get(tag); w.i32_const(TAG_RECORD); w.i32_eq()
        l = w.if_()
        w.local_get(1); w.i32_load(8); w.local_set(n)
        w.i32_const(0); w.local_set(i)
        blk = w.block(); top = w.loop()
        w.local_get(i); w.local_get(n); w.i32_ge_u(); w.br_if(blk)
        w.local_get(out)
        w.local_get(1); w.i32_load(4)
        w.local_get(i); w.i32_const(3); w.i32_shl(); w.i32_add()
        w.i32_load(0)
        w.call(self.h["list_push"]); w.drop()
        w.local_get(i); w.i32_const(1); w.i32_add(); w.local_set(i)
        w.br(top)
        w.end(); w.end()
        w.local_get(out); w.return_()
        w.end()
        self.panic_line_local(w, 0, "I can't loop over that value.")
        w.unreachable()

    # ------------------------------------------------------------------
    # AST -> WASM code generation. Every expression leaves one boxed value
    # (i32) on the stack; every statement leaves the stack empty.
    # ------------------------------------------------------------------
    _BINOP = {
        '+': 0, '-': 1, '*': 2, '/': 3, '%': 4, '**': 5,
        '==': 6, 'is': 6, '!=': 7, 'is not': 7,
        '<': 8, 'is smaller than': 8, '<=': 9, 'is at most': 9,
        '>': 10, 'is bigger than': 10, '>=': 11, 'is at least': 11,
        'and': 12, 'or': 13, 'is in': 14,
    }
    _UNOP = {'not': 0, '-': 1}

    def compile(self, tree):
        """Compile a *checked* AST Program to a WASM binary."""
        self._setup()
        self.builtin_names = {name for name, _, _ in self.BUILTIN_SIGS}
        # Alpha 10: shared closure analysis (niko2/closures.py) drives boxing,
        # capture environments, and the uniform function signature.
        self.closure_info = analyze_closures(tree)
        self.scopes = [{}]      # module scope; name -> ('local'|'global', idx)
        self.loop_stack = []    # (break_lbl, cont_lbl, on_skip) triples;
                              # on_skip is a thunk emitting the loop-index
                              # increment (for/repeat), or None (while).
        self.fn_for = {}        # id(FunctionDef) -> Func
        self.fn_table_idx = {}  # id(FunctionDef) -> funcref table index
        self.cur_info = None    # ClosureInfo of the function being generated
        self._declare_fns(tree.body)
        main = self.m.add_function([], [])
        self.m.add_export("main", "func", main.idx)
        self.m.add_export("memory", "memory", 0)
        self.cur = main
        # Alpha 10: materialize every top-level function as a value in its
        # global slot before any user code runs, so named calls, `set f to
        # add`, recursion, and mutual recursion all resolve through the
        # slots (like the VM's execute(), which installs every non-nested
        # function upfront — including ones inside module-level blocks).
        for _id, info in self.closure_info.items():
            if info.nested:
                continue
            n = info.node
            gidx = self.m.add_global(I32, True, 0)
            self.scopes[0][n.name] = ('global', gidx)
            w = main.w
            w.i32_const(self.fn_table_idx[_id])
            w.i32_const(self.text_val(n.name))
            w.i32_const(len(n.params))
            w.i32_const(0)  # empty env
            w.call(self.h["make_fn"])
            w.global_set(gidx)
        self._gen_block(tree.body)
        main.w.return_()
        # $heap must start after ALL static data, including user literals
        # added during codegen (alloc would otherwise clobber them).
        self.m.globals[self.heap_g] = (
            I32, True, b"\x41" + sleb(self.data_ptr) + b"\x0b")
        return self.m.emit()

    # -- function declaration (pass A: recursion and nesting work) --------
    def _declare_fns(self, stmts):
        # Alpha 10: every Niko function (module-level and nested) gets the
        # uniform (env_ptr, nargs, args_ptr) -> boxed signature and a slot in
        # the single funcref table, in source pre-order.
        for n in stmts:
            if isinstance(n, FunctionDef):
                f = self.m.add_function([I32, I32, I32], [I32])
                self.fn_for[id(n)] = f
                self.fn_table_idx[id(n)] = self.m.add_table_entry(f.idx)
            for child in self._child_blocks(n):
                self._declare_fns(child)

    def _child_blocks(self, n):
        if isinstance(n, FunctionDef):
            yield n.body
        elif isinstance(n, IfStmt):
            for _, b in n.branches:
                yield b
            if n.otherwise:
                yield n.otherwise
        elif isinstance(n, (RepeatStmt, ForStmt, WhileStmt)):
            yield n.body
        elif isinstance(n, MatchStmt):
            for case in n.cases:
                yield case.body
            if n.otherwise:
                yield n.otherwise

    # -- name binding -------------------------------------------------------
    def _is_boxed(self, name):
        """True when `name` is a boxed local of the function being generated
        (Alpha 10): the WASM local holds a cell pointer, not the value."""
        return self.cur_info is not None and name in self.cur_info.boxes

    def _store(self, name, line):
        """Store top-of-stack into `name` in the current scope. Boxed names
        hold cell pointers: the store writes through the cell, so every
        closure sharing the box sees the write (capture by reference)."""
        w = self.cur.w
        sc = self.scopes[-1]
        if name in sc:
            kind, idx = sc[name]
            if kind == 'local' and self._is_boxed(name):
                t = self.cur.new_local()
                w.local_set(t)
                w.local_get(idx); w.local_get(t); w.i32_store(0)
            elif kind == 'local':
                w.local_set(idx)
            else:
                w.global_set(idx)
            return
        # Alpha 10: assigning to a captured name writes through the shared
        # box in our env array (the VM's frame env holds the cells, so its
        # STORE writes through too — never a fresh shadowing local).
        if self.cur_info is not None and name in self.cur_info.captures:
            pos = self.cur_info.captures.index(name)
            t = self.cur.new_local()
            w.local_set(t)  # stack was [V]
            w.local_get(0)  # env_ptr is param 0
            if pos:
                w.i32_const(pos * 4); w.i32_add()
            w.i32_load(0)  # cell pointer
            w.local_get(t)
            w.i32_store(0)
            return
        if len(self.scopes) > 1:
            idx = self.cur.new_local()
            sc[name] = ('local', idx)
            w.local_set(idx)
        else:
            idx = self.m.add_global(I32, True, 0)
            sc[name] = ('global', idx)
            w.global_set(idx)

    def _load(self, name, line):
        w = self.cur.w
        sc = self.scopes[-1]
        if name in sc:
            kind, idx = sc[name]
            if kind == 'local':
                w.local_get(idx)
                if self._is_boxed(name):
                    w.i32_load(0)  # deref the cell
                return
            w.global_get(idx)
            return
        # Module globals are one shared namespace, visible everywhere.
        if len(self.scopes) > 1 and name in self.scopes[0]:
            w.global_get(self.scopes[0][name][1])
            return
        # Alpha 10: captured name — the shared box lives in our env array
        # (env_ptr is param 0), at captures.index(name).
        if self.cur_info is not None and name in self.cur_info.captures:
            pos = self.cur_info.captures.index(name)
            w.local_get(0)
            if pos:
                w.i32_const(pos * 4); w.i32_add()
            w.i32_load(0)  # cell pointer
            w.i32_load(0)  # value
            return
        if name in self.builtin_names:
            raise CompileError(
                f"can't use the builtin '{name}' as a value", line=line)
        if name in BUILTIN_NAMES or name == 'pi':
            raise CompileError(
                f"the WASM backend doesn't support '{name}' yet", line=line)
        raise CompileError(f'I don\'t know what "{name}" is.', line=line)

    # -- statements ----------------------------------------------------------
    def _gen_block(self, stmts):
        for n in stmts:
            self._gen_stmt(n)

    def _gen_stmt(self, n):
        w = self.cur.w
        line = n.line
        if isinstance(n, SetStmt):
            self._gen_expr(n.expr)
            self._store(n.name, line)
        elif isinstance(n, IndexSetStmt):
            w.i32_const(line)
            self._gen_expr(n.target)
            self._gen_expr(n.index)
            self._gen_expr(n.expr)
            w.call(self.h["index_set"])
        elif isinstance(n, AugAssignStmt):
            try:
                op = self._BINOP[n.op]
            except KeyError:
                raise CompileError(f"unknown operator '{n.op}'", line=line)
            w.i32_const(line); w.i32_const(op)
            self._load(n.name, line)
            self._gen_expr(n.expr)
            w.call(self.h["binary"])
            self._store(n.name, line)
        elif isinstance(n, PutStmt):
            w.i32_const(line)
            self._gen_expr(n.target)
            self._gen_expr(n.value)
            w.call(self.h["put_in"])
        elif isinstance(n, RemoveStmt):
            self._gen_expr(n.target)
            self._gen_expr(n.value)
            w.call(self.h["list_remove"])
        elif isinstance(n, AskStmt):
            self._gen_ask(n)
        elif isinstance(n, SayStmt):
            self._gen_say(n)
        elif isinstance(n, AssertStmt):
            self._gen_assert(n)
        elif isinstance(n, ExprStmt):
            self._gen_expr(n.expr)
            w.drop()
        elif isinstance(n, IfStmt):
            end = w.block()
            for cond, body in n.branches:
                self._gen_expr(cond)
                w.call(self.h["truthy"])
                l = w.if_()
                self._gen_block(body)
                w.br(end)
                w.end()
            if n.otherwise:
                self._gen_block(n.otherwise)
            w.end()
        elif isinstance(n, RepeatStmt):
            w.i32_const(line)
            self._gen_expr(n.count)
            w.call(self.h["to_int"])
            cnt = self.cur.new_local(); w.local_set(cnt)
            i = self.cur.new_local(); w.i32_const(0); w.local_set(i)
            brk = w.block(); cont = w.loop()
            # Alpha 15: `skip` must advance the counter before branching
            # back, or the same iteration repeats forever.
            on_skip = lambda i=i: (w.local_get(i), w.i32_const(1),
                                   w.i32_add(), w.local_set(i))
            self.loop_stack.append((brk, cont, on_skip))
            w.local_get(i); w.local_get(cnt); w.i32_ge_s(); w.br_if(brk)
            self._gen_block(n.body)
            w.local_get(i); w.i32_const(1); w.i32_add(); w.local_set(i)
            w.br(cont)
            w.end(); w.end()
            self.loop_stack.pop()
        elif isinstance(n, ForStmt):
            self._gen_for(n)
        elif isinstance(n, WhileStmt):
            brk = w.block(); cont = w.loop()
            self.loop_stack.append((brk, cont, None))
            self._gen_expr(n.cond)
            w.call(self.h["truthy"]); w.i32_eqz(); w.br_if(brk)
            self._gen_block(n.body)
            w.br(cont)
            w.end(); w.end()
            self.loop_stack.pop()
        elif isinstance(n, StopStmt):
            if not self.loop_stack:
                raise CompileError("'stop' outside a loop", line=line)
            w.br(self.loop_stack[-1][0])
        elif isinstance(n, SkipStmt):
            if not self.loop_stack:
                raise CompileError("'skip' outside a loop", line=line)
            brk, cont, on_skip = self.loop_stack[-1]
            if on_skip is not None:
                # Alpha 15: for/repeat advance the index here so the next
                # iteration runs; without this the loop hangs.
                on_skip()
            w.br(cont)
        elif isinstance(n, ReturnStmt):
            if n.expr is None:
                w.i32_const(0)
            else:
                self._gen_expr(n.expr)
            w.return_()
        elif isinstance(n, MatchStmt):
            self._gen_match(n)
        elif isinstance(n, FunctionDef):
            self._gen_function(n)
        elif isinstance(n, UseStmt):
            raise CompileError("the WASM backend doesn't support 'use' yet", line=line)
        else:
            raise CompileError(f"the WASM backend can't compile {type(n).__name__} yet",
                               line=line)

    def _gen_ask(self, n):
        w = self.cur.w
        self._gen_expr(n.prompt)
        w.call(self.h["to_text"])
        t = self.cur.new_local(); w.local_set(t)
        p = self.cur.new_local(); ln = self.cur.new_local()
        v = self.cur.new_local()
        msg = self.cur.new_local()
        fv = self.cur.new_local(F64) if n.want_number else None
        done = w.block(); again = w.loop()
        w.local_get(t); w.i32_load(4); w.local_get(t); w.i32_load(8)
        w.call(self.imp["input"])
        w.local_set(ln); w.local_set(p)
        if n.want_number:
            w.local_get(p); w.local_get(ln); w.call(self.imp["parse_num"])
            w.local_set(fv)
            w.local_get(fv); w.local_get(fv); w.f64_ne()
            l = w.if_()
            w.i32_const(self.text_val("Please type a number.")); w.local_set(msg)
            w.local_get(msg); w.i32_load(4)
            w.local_get(msg); w.i32_load(8)
            w.call(self.imp["print"])
            w.br(again)
            w.end()
            w.local_get(fv); w.call(self.h["make_number"]); w.local_set(v)
        else:
            w.local_get(p); w.local_get(ln); w.call(self.h["make_text"])
            w.local_set(v)
        w.br(done)
        w.end(); w.end()
        w.local_get(v)
        self._store(n.name, n.line)

    def _gen_say(self, n):
        w = self.cur.w
        w.call(self.h["sb_new"])
        sb = self.cur.new_local(); w.local_set(sb)
        for k, e in enumerate(n.exprs):
            if k:
                w.local_get(sb)
                w.i32_const(self.text_val(" "))
                w.call(self.h["sb_push_text"])
            w.local_get(sb)
            self._gen_expr(e)
            w.call(self.h["to_text"])
            w.call(self.h["sb_push_text"])
        w.local_get(sb); w.call(self.h["sb_finish"]); w.local_set(sb)
        w.local_get(sb); w.i32_load(4)
        w.local_get(sb); w.i32_load(8)
        w.call(self.imp["print"])

    def _gen_assert(self, n):
        # Alpha 27: assert COND [, MSG]. If the condition is falsy, panic
        # with "Line <n>: Assertion failed: "<src>" is not true[": <msg>"]".
        # The message expression is only evaluated on the failure path,
        # mirroring the VM compiler's lazy codegen.
        w = self.cur.w
        end = w.block()
        self._gen_expr(n.cond)
        w.call(self.h["truthy"])
        l = w.if_()
        w.br(end)
        w.end()
        prefix = f'Assertion failed: "{n.source}" is not true'
        if n.message is None:
            self.emit_panic_line(w, n.line, prefix + ".")
        else:
            t = self.cur.new_local()
            w.i32_const(self.text_val(prefix + ": "))
            self._gen_expr(n.message)
            w.i32_const(self.text_val(""))
            w.call(self.h["concat3"])
            w.local_set(t)
            w.i32_const(n.line)
            w.local_get(t); w.i32_load(4)
            w.local_get(t); w.i32_load(8)
            w.call(self.h["panic_line"])
            w.unreachable()
        w.end()

    def _gen_for(self, n):
        w = self.cur.w
        w.i32_const(n.line)
        self._gen_expr(n.iterable)
        w.call(self.h["to_iter_list"])
        it = self.cur.new_local(); w.local_set(it)
        i = self.cur.new_local(); w.i32_const(0); w.local_set(i)
        brk = w.block(); cont = w.loop()
        # Alpha 15: `skip` advances the index before branching back.
        on_skip = lambda i=i: (w.local_get(i), w.i32_const(1),
                               w.i32_add(), w.local_set(i))
        self.loop_stack.append((brk, cont, on_skip))
        # n is re-read each pass so `put` inside the loop behaves like the VM
        w.local_get(i); w.local_get(it); w.i32_load(8); w.i32_ge_u(); w.br_if(brk)
        w.local_get(it); w.i32_load(4)
        w.local_get(i); w.i32_const(2); w.i32_shl(); w.i32_add()
        w.i32_load(0)
        self._store(n.name, n.line)
        self._gen_block(n.body)
        w.local_get(i); w.i32_const(1); w.i32_add(); w.local_set(i)
        w.br(cont)
        w.end(); w.end()
        self.loop_stack.pop()

    def _gen_match(self, n):
        w = self.cur.w
        self._gen_expr(n.expr)
        subj = self.cur.new_local(); w.local_set(subj)
        end = w.block()
        for case in n.cases:
            for p in case.patterns:
                self._gen_match_arm(p, subj, 0, end, case)
        if n.otherwise:
            self._gen_block(n.otherwise)
        w.end()

    def _gen_match_expr(self, n):
        # Alpha 12: match as an expression. Mirrors _gen_match: the
        # subject is evaluated once; the winning arm's final expression
        # value is stored into a fresh result local; the local is loaded
        # at the end so the value is left on the stack as the
        # expression's value. Fresh locals per call keep nested match
        # expressions safe.
        w = self.cur.w
        self._gen_expr(n.expr)
        subj = self.cur.new_local(); w.local_set(subj)
        result = self.cur.new_local()
        # Pre-initialize the result to nothing as defensive insurance,
        # mirroring the VM's $matchval initialization.
        self._push_literal(None, n.line)
        w.local_set(result)
        end = w.block()
        for case in n.cases:
            for p in case.patterns:
                self._gen_match_arm(p, subj, 0, end, case, result=result)
        if n.otherwise:
            self._gen_arm_value(n.otherwise, result, n.line)
        w.end()
        w.local_get(result)

    def _gen_arm_value(self, body, result, line):
        # Compile an arm body, storing the final expression's value into
        # the result local. The checker guarantees the body ends with an
        # ExprStmt; re-verify here defensively.
        if not body or not isinstance(body[-1], ExprStmt):
            raise CompileError('match arm must end with an expression to produce a value',
                               line=line)
        for s in body[:-1]:
            self._gen_stmt(s)
        self._gen_expr(body[-1].expr)
        self.cur.w.local_set(result)

    def _gen_match_arm(self, p, slot, depth, end, case, result=None):
        # Alpha 11: guards + list/record patterns. Each test condition opens
        # a nested if_ (short-circuiting exactly like the VM's JUMP_IF_FALSE
        # chain); at the innermost point the bindings are performed, then the
        # guard (if any) runs in its own if_, then the body. A failed test or
        # guard simply falls out of the nested ifs to the next pattern.
        w = self.cur.w
        opened = [0]

        def cond(emit):
            emit()
            w.if_()
            opened[0] += 1

        self._pat_test(p, slot, depth, cond)
        self._pat_bind(p, slot, depth)

        def body():
            if result is None:
                self._gen_block(case.body)
            else:
                self._gen_arm_value(case.body, result, case.line)
            w.br(end)

        if case.guard is not None:
            self._gen_expr(case.guard)
            w.call(self.h["truthy"])
            w.if_()
            body()
            w.end()
        else:
            body()
        for _ in range(opened[0]):
            w.end()

    def _pat_test(self, p, slot, depth, cond):
        # Emit the test for pattern p against the value in WASM local
        # `slot`. Each condition is passed to cond(), which emits it and
        # opens a nested if_. Sub-values for nested patterns are stored into
        # fresh temp locals by always-true conditions so the stores only run
        # after the enclosing checks passed.
        w = self.cur.w
        if isinstance(p, MatchLit):
            def c(p=p, slot=slot):
                w.local_get(slot)
                self._push_literal(p.value, p.line)
                w.call(self.h["equals"])
            cond(c)
        elif isinstance(p, MatchBind):
            pass
        elif isinstance(p, MatchOk):
            def c(slot=slot):
                w.local_get(slot); w.i32_load(0); w.i32_const(TAG_RESULT); w.i32_eq()
                w.local_get(slot); w.i32_load(4); w.i32_const(1); w.i32_eq()
                w.i32_and()
            cond(c)
        elif isinstance(p, MatchErr):
            def c(slot=slot):
                w.local_get(slot); w.i32_load(0); w.i32_const(TAG_RESULT); w.i32_eq()
                w.local_get(slot); w.i32_load(4); w.i32_const(0); w.i32_eq()
                w.i32_and()
            cond(c)
        elif isinstance(p, MatchList):
            nfixed = sum(1 for i in p.items if not isinstance(i, MatchRest))
            has_rest = nfixed != len(p.items)

            def ctag(slot=slot):
                w.local_get(slot); w.i32_load(0); w.i32_const(TAG_LIST); w.i32_eq()
            cond(ctag)

            def clen(slot=slot, nfixed=nfixed, has_rest=has_rest):
                w.local_get(slot); w.call(self.h["list_len"])
                w.i32_const(nfixed)
                if has_rest:
                    w.i32_ge_u()
                else:
                    w.i32_eq()
            cond(clen)
            for i, item in enumerate(p.items):
                if isinstance(item, MatchRest):
                    continue
                sub = self.cur.new_local()

                def cstore(slot=slot, i=i, sub=sub):
                    w.local_get(slot); w.i32_const(i); w.call(self.h["list_get0"])
                    w.local_set(sub)
                    w.i32_const(1)
                cond(cstore)
                self._pat_test(item, sub, depth + 1, cond)
        elif isinstance(p, MatchRecord):
            def ctag(slot=slot):
                w.local_get(slot); w.i32_load(0); w.i32_const(TAG_RECORD); w.i32_eq()
            cond(ctag)
            for key, subp in p.fields:
                sub = self.cur.new_local()

                def chas(slot=slot, key=key):
                    w.local_get(slot)
                    w.i32_const(self.text_val(key))
                    w.call(self.h["record_find"])
                    w.i32_const(0); w.i32_ge_s()
                cond(chas)

                def cget(p=p, slot=slot, key=key, sub=sub):
                    w.i32_const(p.line); w.local_get(slot)
                    w.i32_const(self.text_val(key))
                    w.call(self.h["record_get"])
                    w.local_set(sub)
                    w.i32_const(1)
                cond(cget)
                self._pat_test(subp, sub, depth + 1, cond)
        else:
            raise CompileError("bad pattern", line=p.line)

    def _pat_bind(self, p, slot, depth):
        # Perform the bindings for pattern p, whose subject value is in WASM
        # local `slot`. Only called after the test passed.
        w = self.cur.w
        if isinstance(p, MatchBind):
            w.local_get(slot)
            self._store(p.name, p.line)
        elif isinstance(p, (MatchOk, MatchErr)):
            w.local_get(slot); w.i32_load(8)
            self._store(p.name, p.line)
        elif isinstance(p, MatchLit):
            pass
        elif isinstance(p, MatchList):
            for i, item in enumerate(p.items):
                if isinstance(item, MatchRest):
                    self._gen_list_slice(slot, i)
                    self._store(item.name, p.line)
                else:
                    w.local_get(slot); w.i32_const(i); w.call(self.h["list_get0"])
                    sub = self.cur.new_local(); w.local_set(sub)
                    self._pat_bind(item, sub, depth + 1)
        elif isinstance(p, MatchRecord):
            for key, subp in p.fields:
                w.i32_const(p.line); w.local_get(slot)
                w.i32_const(self.text_val(key))
                w.call(self.h["record_get"])
                sub = self.cur.new_local(); w.local_set(sub)
                self._pat_bind(subp, sub, depth + 1)
        else:
            raise CompileError("bad pattern", line=p.line)

    def _gen_list_slice(self, slot, start):
        # Push list[slot][start:] (0-based start) as a fresh list value.
        w = self.cur.w
        w.call(self.h["list_new"])
        out = self.cur.new_local(); w.local_set(out)
        w.local_get(slot); w.call(self.h["list_len"])
        n = self.cur.new_local(); w.local_set(n)
        i = self.cur.new_local(); w.i32_const(start); w.local_set(i)
        brk = w.block(); top = w.loop()
        w.local_get(i); w.local_get(n); w.i32_ge_u(); w.br_if(brk)
        w.local_get(out)
        w.local_get(slot); w.local_get(i); w.call(self.h["list_get0"])
        w.call(self.h["list_push"])
        w.drop()
        w.local_get(i); w.i32_const(1); w.i32_add(); w.local_set(i)
        w.br(top)
        w.end(); w.end()
        w.local_get(out)

    def _capture_cell(self, info, cname):
        """Push the cell pointer for captured name `cname` (used when a
        nested `to` statement builds its env array). `info` is the
        ClosureInfo of the enclosing function; scopes[-1] is its scope."""
        w = self.cur.w
        if cname in info.boxes:
            # own boxed local: the WASM local already holds the cell pointer
            w.local_get(self.scopes[-1][cname][1])
            return
        # pass-through: the box lives in our own env array (param 0)
        pos = info.captures.index(cname)
        w.local_get(0)
        if pos:
            w.i32_const(pos * 4); w.i32_add()
        w.i32_load(0)  # cell pointer (the env holds cells, no deref)

    def _gen_function(self, n):
        # Alpha 10: first-class functions with capture-by-reference.
        info = self.closure_info[id(n)]
        f = self.fn_for[id(n)]
        enclosing_info = self.cur_info
        if info.nested:
            # MAKE_FUNCTION, emitted into the enclosing function: allocate
            # the env array, fill it with the current cells for
            # captures(info), build the function value, store it to the name.
            # Cells already exist (boxed locals are allocated at function
            # entry), so every `to` execution in a loop shares the same box
            # — Python semantics, matching the VM's wrap-or-create.
            w = self.cur.w
            w.i32_const(self.fn_table_idx[id(n)])
            w.i32_const(self.text_val(n.name))
            w.i32_const(len(n.params))
            ncaps = len(info.captures)
            if ncaps:
                w.i32_const(ncaps * 4)
                w.call(self.h["alloc"])
                env = self.cur.new_local()
                w.local_set(env)
                for i, cname in enumerate(info.captures):
                    w.local_get(env)
                    self._capture_cell(enclosing_info, cname)
                    w.i32_store(i * 4)
                w.local_get(env)
            else:
                w.i32_const(0)  # empty env
            w.call(self.h["make_fn"])
            self._store(n.name, n.line)
        # Module-level functions are materialized in main's prologue; their
        # `to` statements emit no runtime code, just the body below.
        old = self.cur
        self.scopes.append({})
        self.cur, self.cur_info = f, info
        w = f.w
        # Uniform signature: params are (env_ptr, nargs, args_ptr).
        for k, p in enumerate(n.params):
            pname = p.split(':', 1)[0].strip()
            pl = f.new_local()
            w.local_get(2)
            if k:
                w.i32_const(k * 4); w.i32_add()
            w.i32_load(0)  # args[k]: boxed value pointer
            if pname in info.boxes:
                w.call(self.h["box_new"])  # param captured: box it eagerly
            w.local_set(pl)
            self.scopes[-1][pname] = ('local', pl)
        # Every other boxed name (assigned locals, nested def names captured
        # from here) gets a fresh cell per call, initialized to nothing.
        for bname in info.boxes:
            if bname not in self.scopes[-1]:
                bl = f.new_local()
                w.i32_const(self.nothing_addr)
                w.call(self.h["box_new"])
                w.local_set(bl)
                self.scopes[-1][bname] = ('local', bl)
        self._gen_block(n.body)
        w.i32_const(self.nothing_addr); w.return_()
        self.cur, self.cur_info = old, enclosing_info
        self.scopes.pop()

    # -- expressions ----------------------------------------------------------
    def _push_literal(self, v, line):
        w = self.cur.w
        if v is None:
            w.i32_const(self.nothing_addr)
        elif v is True:
            w.i32_const(self.yes_addr)
        elif v is False:
            w.i32_const(self.no_addr)
        elif isinstance(v, float):
            w.f64_const(v); w.call(self.h["make_number"])
        elif isinstance(v, int):
            w.f64_const(float(v)); w.call(self.h["make_number"])
        elif isinstance(v, str):
            w.i32_const(self.text_val(v))
        else:
            raise CompileError(f"the WASM backend can't compile that literal yet",
                               line=line)

    def _gen_expr(self, n):
        w = self.cur.w
        line = n.line
        if isinstance(n, LiteralExpr):
            self._push_literal(n.value, line)
        elif isinstance(n, NameExpr):
            if n.name == 'pi':
                w.f64_const(math.pi); w.call(self.h["make_number"])
            else:
                self._load(n.name, line)
        elif isinstance(n, ListExpr):
            w.call(self.h["list_new"])
            for x in n.items:
                self._gen_expr(x)
                w.call(self.h["list_push"])
        elif isinstance(n, RecordExpr):
            w.call(self.h["record_new"])
            r = self.cur.new_local(); w.local_set(r)
            for k, v in n.items:
                w.local_get(r)
                w.i32_const(self.text_val(k))
                self._gen_expr(v)
                w.call(self.h["record_set"])
            w.local_get(r)
        elif isinstance(n, IndexExpr):
            w.i32_const(line)
            self._gen_expr(n.obj)
            self._gen_expr(n.index)
            w.call(self.h["index_get"])
        elif isinstance(n, AttrExpr):
            w.i32_const(line)
            self._gen_expr(n.obj)
            w.i32_const(self.text_val(n.name))
            w.call(self.h["attr"])
        elif isinstance(n, UnaryExpr):
            try:
                op = self._UNOP[n.op]
            except KeyError:
                raise CompileError(f"unknown operator '{n.op}'", line=line)
            w.i32_const(line); w.i32_const(op)
            self._gen_expr(n.expr)
            w.call(self.h["unary"])
        elif isinstance(n, BinaryExpr):
            if n.op in ('and', 'or'):
                # Alpha 30: short-circuit with Niko 1 (Python) semantics;
                # see _gen_short_circuit.
                self._gen_short_circuit(n)
            else:
                try:
                    op = self._BINOP[n.op]
                except KeyError:
                    raise CompileError(f"unknown operator '{n.op}'", line=line)
                w.i32_const(line); w.i32_const(op)
                self._gen_expr(n.left)
                self._gen_expr(n.right)
                w.call(self.h["binary"])
        elif isinstance(n, CallExpr):
            self._gen_call(n)
        elif isinstance(n, MatchExpr):
            self._gen_match_expr(n)
        else:
            raise CompileError(f"the WASM backend can't compile {type(n).__name__} yet",
                               line=line)

    def _gen_short_circuit(self, n):
        # Alpha 30: short-circuit `and`/`or` with Niko 1 (Python)
        # semantics: `a and b` evaluates `a`; if falsy the result is `a`
        # and `b` is never evaluated, otherwise the result is `b`.
        # `a or b` evaluates `a`; if truthy the result is `a` and `b` is
        # never evaluated, otherwise the result is `b`. Truthiness goes
        # through the `truthy` helper (same definition as the VM: no,
        # nothing, 0, 0.0, "", [], {} are falsy; everything else truthy).
        # Follows the _gen_match_expr convention: an empty block plus a
        # fresh result local merges the two paths so exactly one value
        # pointer is left on the stack (the writer's block() carries no
        # result type, so the value travels through the local). The
        # dropped left pointer needs no freeing: bump-allocated linear
        # memory. Label depth stays correct because the block is opened
        # and closed inside this call; the loop_stack (stop/skip) labels
        # are absolute label objects resolved by the writer.
        w = self.cur.w
        self._gen_expr(n.left)
        result = self.cur.new_local(); w.local_set(result)
        end = w.block()
        w.local_get(result)
        w.call(self.h["truthy"])
        if n.op == 'and':
            w.i32_eqz()  # skip the right side when the left is falsy
        w.br_if(end)     # `or` skips the right side when left is truthy
        self._gen_expr(n.right)
        w.local_set(result)
        w.end()
        w.local_get(result)

    def _gen_call(self, n):
        # Alpha 10: every call site evaluates the callee to a value and goes
        # through call_fn: tag check (`I can't call <v> as a function.`),
        # arity check (`<name> expected <p> arguments, got <n>.`), then
        # call_indirect with the uniform (env_ptr, nargs, args_ptr)
        # signature. Builtins keep their direct calls (the checker rejects
        # builtins as values, so they never reach _load).
        w = self.cur.w
        line = n.line
        fn = n.fn
        if isinstance(fn, NameExpr):
            name = fn.name
            if name == 'pi':
                raise CompileError("'pi' is a value, not a function", line=line)
            if name in self.builtin_names:
                self._gen_builtin_call(name, n.args, line)
                return
            if name in BUILTIN_NAMES:
                raise CompileError(
                    f"the WASM backend doesn't support '{name}' yet", line=line)
            self._load(name, line)
        else:
            # first-class callee: attribute/index results (e.g. m.add(2, 3)),
            # call results, etc. -- _gen_expr leaves the value on the stack
            # and call_fn checks the tag at runtime.
            self._gen_expr(fn)
        # stack: [fnval] — stash it, then build the args array (fn first,
        # then args, matching the VM's evaluation order)
        nargs = len(n.args)
        fv = self.cur.new_local()
        w.local_set(fv)
        w.i32_const(nargs * 4)
        w.call(self.h["alloc"])
        ap = self.cur.new_local()
        w.local_set(ap)
        for i, a in enumerate(n.args):
            w.local_get(ap)
            self._gen_expr(a)
            w.i32_store(i * 4)
        w.i32_const(line)
        w.local_get(fv)
        w.i32_const(nargs)
        w.local_get(ap)
        w.call(self.h["call_fn"])

    def _gen_builtin_call(self, name, args, line):
        w = self.cur.w
        if name in ('max', 'min'):
            w.i32_const(line)
            w.call(self.h["list_new"])
            for a in args:
                self._gen_expr(a)
                w.call(self.h["list_push"])
            w.call(self.h["b_" + name])
            return
        params = [p for p in self.BUILTIN_SIGS
                  if p[0] == name][0][1]
        if name == 'split' and len(args) == 1:
            w.i32_const(line)
            self._gen_expr(args[0])
            w.i32_const(self.text_val(" "))
            w.call(self.h["b_split"])
            return
        if len(args) != len(params):
            raise CompileError(
                f"'{name}' takes {len(params)} argument(s) ({len(args)} given)",
                line=line)
        w.i32_const(line)
        for a in args:
            self._gen_expr(a)
        w.call(self.h["b_" + name])


class WasmBackend(Backend):
    name = "wasm"
    description = "Compile Niko to a WebAssembly binary (boxed values, host imports)."

    @property
    def output_extension(self):
        return ".wasm"

    def compile(self, tree):
        return WasmCompiler().compile(tree)
