# Alpha 11 design: match guards + list/record patterns

## Surface syntax

```
match VALUE:
    when [a, b] if a is bigger than b:
        say "a wins"
    when [first, ...rest]:
        say "first is " + text(first) + ", rest has " + text(length(rest))
    when []:
        say "empty"
    when {name: n, age: a} if a is at least 18:
        say n + " is an adult"
    when ok v:
        say v
    otherwise:
        say "no match"
```

### Guards
`when <patterns> if <expr>:` — the guard applies to the whole arm (all
comma-separated alternatives). Evaluation order per pattern, in order:

1. evaluate the subject once (into the existing hidden `$match` slot)
2. test the pattern against the subject
3. if it matches, perform the bindings
4. evaluate the guard expression (it may reference the pattern's bindings)
5. if the guard is true, run the arm body; otherwise fall through to the
   next pattern/arm exactly as if the pattern had not matched

A guard must be a boolean expression (same rule as `if`/`while`
conditions). `when` without `if` is unchanged.

### List patterns
`[p1, p2, ...]` matches a list of exactly that many items; each element is
itself a pattern (usually a bare binding, but literals and nested
list/record patterns work). `[]` matches only the empty list.
`[head, ...tail]` — `...name` must be the last element; it binds the
remaining items (possibly empty) as a list. `[...all]` binds the whole list.

A list pattern against a non-list subject simply does not match (falls
through); the typechecker rejects it statically when the subject's type is
known to be non-list. Element bindings get the list's element type
(`list<number>` → elements are `number`); `...rest` binds `list<elem>`.

### Record patterns
`{key: pattern, ...}` matches a record that has all the named keys; each
key binds through its sub-pattern (usually a bare binding). Keys are
identifiers or quoted strings, mirroring record literal syntax. Extra keys
on the subject are fine. A record pattern against a non-record subject does
not match; the typechecker rejects it statically when the subject's type is
known to be non-record. Record fields type as `any` (Niko records are
`map`, without static field types).

Patterns nest arbitrarily: `when [{name: n}, ...rest]:`, `when [ok v]:`.

### Not in scope
Match-as-expression (still future work). `$match` slot mechanics unchanged.
Existing arm semantics unchanged.

## AST (niko2/ast.py)

```python
class MatchCase(Node): patterns:list; guard:object|None=None; body:list
class MatchList(Node): items:list          # sub-patterns; may end with MatchRest
class MatchRest(Node): name:str            # only as last item of MatchList
class MatchRecord(Node): fields:list       # list of (key:str, sub-pattern)
```

`MatchStmt.cases` becomes `list[MatchCase]` (was list of `(patterns, body)`
tuples). All unpack sites updated: typecheck.py (2x), compiler.py,
backends/wasm.py, backends/native.py, formatter.py, symbols.py,
closures.py (3x).

## Typechecker (niko2/typecheck.py)

`match_pat(p, t, line)` gains:
- `MatchList`: if `t` is known and `base_type(t) != 'list'` → error
  `cannot match [...] against {t}`. `elem = t.arg` when `t` is `Type('list', arg)`
  else `ANY`. Recurse into each item with `elem`; `MatchRest` defines its
  name as `Type('list', elem)`.
- `MatchRecord`: if `t` is known and `base_type(t) != 'map'` → error
  `cannot match {...} against {t}`. Recurse into each field sub-pattern with `ANY`.
- Recursion handles nesting; `MatchLit` still binds nothing.

Arm checking order: define all pattern bindings first (so the guard may
reference them), then check the guard expression and require boolean
(`while`/`if` rule: must be boolean, `ANY` allowed), then check the body.

Parser errors (all `ParseError` with line/column):
- `...name` not last in a list pattern
- `...` with a bad name
- record field without `key: pattern` shape, or a bad key
- empty pattern alternatives (existing)

## VM (niko2/compiler.py + niko2/vm.py)

New opcodes (all tiny, documented in vm.py):
- `IS_LIST`: pop value, push `isinstance(value, list)`
- `IS_RECORD`: pop value, push `isinstance(value, dict)`
- `LIST_SLICE`: pop 1-based start, pop list, push `list[start-1:]`
  (runtime error if not a list — cannot happen after IS_LIST, but be safe)

`match_test(p, b)` (leaves yes/no on the stack) becomes recursive over a
*subject slot*: the top-level pattern tests `$match`; nested patterns test
hidden per-depth slots `$pat0`, `$pat1`, ... (names are not valid user
identifiers, so they can never be captured or referenced).

- `MatchList` (no rest): `IS_LIST($match) and length($match) == n`, where the
  length check is emitted with existing ops (`LOAD 'length'`, `CALL 1`,
  `PUSH_CONST n`, `BINARY '=='`, `BINARY 'and'`). With rest: `length >= n`.
  Nested element tests recurse: for each element sub-pattern that needs a
  test (list/record sub-patterns), store element into `$pat{d+1}` and emit
  its test, `and`-ed in.
- `MatchLit`/`MatchBind`/`MatchOk`/`MatchErr`: unchanged.
- `MatchRecord`: `IS_RECORD($match) and has($match, "k1") and has($match, "k2")...`
  using the existing `has` builtin (`x in c` works for dicts). `{}` (no fields)
  tests just `IS_RECORD`.

`match_bind(p, b)` becomes recursive over the same slots:
- `MatchList`: for i, sub: `LOAD $pat{d}; PUSH_CONST i+1; INDEX; <bind sub>`
  (Niko lists are 1-based; `INDEX` already adjusts). Rest: `LOAD $pat{d};
  PUSH_CONST n+1; LIST_SLICE; STORE name`.
- `MatchRecord`: for each `(key, sub)`: `LOAD $pat{d}; PUSH_CONST key; INDEX;
  <bind sub>` (`INDEX` on a dict does the key lookup; the test already
  guaranteed the key exists).
- `MatchLit`: nothing. `MatchBind`/`MatchOk`/`MatchErr`: unchanged.

`match_stmt` per arm, per pattern:
```
jf1 = match_test(p)          # JUMP_IF_FALSE patch
match_bind(p)
if arm.guard:
    self.expr(arm.guard, b)
    jf2 = b.emit('JUMP_IF_FALSE', None, line)
body...
exits.append(JUMP end)
b.patch(jf1, ...); b.patch(jf2, ...) if guard
```
Bindings happen before the guard, so the guard sees them. A failed guard
falls through to the next pattern exactly like a failed test.

Note: pattern bindings are plain `STORE`s, so Alpha 10 boxing/closures work
over them unchanged — provided `niko2/closures.py` records the new binding
writes and the guard's reads (see below).

## WASM backend (niko2/backends/wasm.py)

Mirror in `_gen_match`:
- `MatchList`: subject tag check (`TAG_LIST`), length via the existing
  list-length helper, element access via the existing list-item helper,
  rest via a new or existing slice helper (loop `list_new` + push, or add a
  host import — implementer's choice, must be byte-identical to the VM).
- `MatchRecord`: tag check (`TAG_RECORD`/`TAG_MAP` — check the constant name),
  key presence via the existing record-get helper (null/missing sentinel? if
  the helper panics on missing keys, add a `has`-style check first —
  implementer's choice).
- Guards: evaluate after bindings inside the arm's `if_` block; on false,
  skip the body (structure: nested `if_` for the guard, or `br_if` past the
  body — implementer's choice).
- Raise `CompileError("bad pattern")` for unknown nodes, as today.

## Native backend (niko2/backends/native.py + niko_runtime.c/.h)

`_gen_match` is restructured: the current `if/else if` chain cannot host
bind-then-guard (the guard must be evaluated after the bindings, so it
cannot be part of the `if (...)` condition). New shape per pattern:

```c
if (<test>) {
    <binds>
    if (<guard>) {          // only when the arm has a guard
        <body>
        goto match_end_N;
    }
}
```

Semantics are unchanged (first match wins via `goto`), just no `else`.
- `MatchList`: `ts->tag == TAG_LIST` (check constant name; function is 7) and
  `nval_list_len(ts) == n` (or `>= n` with rest). Elements via
  `nval_list_item(ts, i)` — check 0/1-based in the runtime. Rest: add
  `nval_list_slice(NVal *list, int64_t from)` (1-based, like Niko indexing)
  to `niko_runtime.c/.h`.
- `MatchRecord`: tag check + key presence. Add `nval_record_has(NVal*, const char*)`
  if no equivalent exists (check the header first).
- Nested patterns: use fresh `NVal *` temps (`self._tmp()`), e.g.
  `NVal *t3 = nval_list_item(ts, 0);` then test/bind against the temp.
- Guard: `nval_truthy(<gen_expr(guard)>)`.

## Shared tooling

- `niko2/closures.py` (3 sites): treat `MatchCase` (`case.patterns`,
  `case.guard`, `case.body`); collect binding *writes* recursively from all
  pattern nodes (MatchBind/MatchOk/MatchErr names, MatchRest names, nested
  list/record bindings); record *reads* from `case.guard` expressions after
  the pattern writes (a guard reading a pattern-bound name resolves to the
  binding, not an enclosing capture).
- `niko2/symbols.py`: define all bindings recursively (kind `'match binding'`,
  type `'unknown'`); `walk_expr` the guard.
- `niko2/formatter.py`: `format_pattern` gains `MatchList` (`[a, b]`,
  `[head, ...tail]`), `MatchRecord` (`{name: n, age: a}`); arm line becomes
  `when <pats>[ if <guard>]:`.
- `niko2/lsp.py`: no new keywords (`if` already one) — no change expected.
- `niko2/debug.py` / `nikoir.py`: no match handling — no change expected.

## Tests

- `tests/niko2_cases/match_patterns.niko` (+`.out`): VM-suite coverage —
  guards (pass, fallthrough, referencing bindings, with alternatives),
  list patterns (fixed, empty, nested, rest, fallthrough on wrong
  length/type), record patterns (fields, missing key, nested, extra keys ok).
- `tests/test_match_patterns.py`: checker rejections with line numbers
  (list pattern vs known number, record pattern vs known list, guard not
  boolean), malformed patterns (rest not last, bad record field, `...` bad
  name), plus a 3-way differential (VM/WASM/native byte-identical; skip
  cleanly without node/cc) reusing the `tests/test_closures.py` differential
  harness style.
- Full suite green: 26 niko1, all niko2 cases + nikoir round trip, all
  `tests/test_*.py`.

## Docs

- `RELEASE_NOTES_ALPHA11.md` (new), `NIKO_AI_HANDOFF.md` §3b area,
  `NEXT_STEPS.md` (Alpha 11 complete), `niko2/KNOWN_LIMITATIONS.md` (move
  guards/list/record patterns from future work to done),
  `NIKO_AI_BRIEF.md` §3b (user-visible syntax).
