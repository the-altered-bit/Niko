# Niko 1 → Niko 2 Migration Guide

Niko 2 is a rewrite of Niko 1 with a real parser, a typechecker, and three
backends (VM, WASM, native). Most Niko 1 programs need small adjustments
before they run on Niko 2. This guide catalogs every difference, and
`niko2 migrate` finds them for you:

```
niko2 migrate program.niko          # report every issue, with line numbers
niko2 migrate --fix program.niko    # apply the mechanical fixes in place
```

Each finding cites a section below (e.g. `[cond-boolean]` → §2.1).
`--fix` only applies rewrites with identical semantics; everything else
is reported for you to decide (§8).

## 1. How to read this guide

- **Error** — Niko 2 rejects the program. It will not run until you fix it.
- **Warning** — Niko 2 runs it, but it may behave differently. Read the
  section and decide whether it matters for your program.
- **Note** — no action needed; recorded so you don't go looking.

`niko2 migrate` works in two phases: a heuristic scan for the idioms
below, then Niko 2's own parser + typechecker for anything the scan can't
name. If Niko 2 accepts your file outright, error findings are dropped
automatically (a clean check is ground truth) — except `use <python-lib>`
(§3.2) and `join(list)` (§3.18), which Niko 2 accepts but which still
break at run time.

## 2. Hard errors — things Niko 2 rejects

### 2.1 Conditions must be boolean

Niko 1 used Python truthiness: any value could guard an `if`/`while`.
Niko 2's typechecker requires the condition to be a boolean.

```niko
# Niko 1
if name:
if count:
while line:
```

```niko
# Niko 2
if name is not "":
if count is not 0:
while line is not "":
```

A call is fine if it returns a boolean (`if is_ready():` typechecks);
`migrate` asks Niko 2 itself and won't flag those. String and number
literals (`if "x":`, `if 0:`) are always errors.

### 2.2 `not` needs a boolean operand

```niko
# Niko 1
if not name:     # Python: not ""
```

```niko
# Niko 2 -- parenthesize a comparison
if not (name is ""):
```

### 2.3 Builtins are not values

Niko 1 let you alias builtins (`set f to text`). Niko 2's builtins only
exist in call position; naming one as a value is a type error. (The
constant `pi` is exempt — it *is* a value.) Either call it directly or
wrap it:

```niko
to shout with s:
  give back upper(s)
```

Names like `say`, `ask`, `remove`, `range`, `random` aren't builtins in
Niko 2 at all (they're statements or renamed, §§3.3–3.6), so aliasing
them fails too.

### 2.4 Operand types are checked

Niko 1 leaned on Python: `yes + 1` computed with 1/0, and `"ab" * 3`
repeated the string. Niko 2's arithmetic needs numbers and has no string
repetition — both are type errors. Write `1`/`0` explicitly, and repeat
in a loop.

### 2.5 Reading Niko 2's own error messages

When `migrate` can't name a problem more precisely it quotes Niko 2
directly (`[n2-diagnostic]`). The caret diagnostics from Alpha 5 show the
exact line and column; fix what's under the caret first, then re-run
`migrate` — one fix often clears several follow-on errors.

## 3. Niko 1 syntax Niko 2 doesn't accept

Niko 1 was a line-oriented transpiler: anything it didn't recognize fell
through to Python verbatim. Niko 2 has a real grammar and no Python
fallthrough, so every Pythonism below is a parse error.

### 3.1 `repeat forever:` → `while yes:`

```niko
while yes:
  ...
```

(`--fix` rewrites this.)

### 3.2 `use` no longer imports Python

Niko 1's `use math` meant `import math`. Niko 2's `use` only includes
other *Niko* files (`use "helpers.niko"`), so `use math` is **silently
ignored** — the most dangerous item in this guide, because nothing fails
until the missing name is used. Common replacements:

| Niko 1 | Niko 2 |
|---|---|
| `use math` | builtins (`sqrt`, `pi`, `floor`, `ceil`, `abs`, `round`) or `import "stdlib/math.niko" as m` |
| `use json` | `import "stdlib/json.niko" as json` (`json.parse` / `json.stringify`) |
| `use random` / `use time` | builtins (`random_int`, `random A to B`, `sleep`, `today`, `now`) |
| `use os` / `use sys` / `use re` | no equivalent — file work uses `read_file` / `write_file` / `append_file` / `file_exists` |

Also: `use` (and `import`) must be at the top of the file in Niko 2, never
inside a function — move them out.

### 3.3 `ask(...)` → the `ask` statement

```niko
# Niko 1
set name to ask("What is your name?")
set age to ask_number("Age?")

# Niko 2
ask "What is your name?" into name
ask number "Age?" into age
```

(`--fix` rewrites the `set x to ask(...)` shape.)

### 3.4 `remove(list, x)` → the `remove` statement

```niko
remove x from list
```

(`--fix` rewrites this. Note the argument order flips.)

### 3.5 `random(a, b)` → `random a to b`

Niko 2 also offers `random_int(a, b)` as a builtin call. (`--fix`
rewrites the call form to the `random A to B` syntax.)

### 3.6 `range(a, b)` → `niko_range(a, b)`

`range` was renamed to avoid the clash with the statement-ish feel of
the language. Semantics are unchanged: inclusive on both ends, and
`niko_range(5, 1)` counts down automatically. (`--fix` renames it.)

### 3.7 `in` → `is in`, `not in` → `not (x is in y)`

Niko 2 has no bare `in` operator (and no `is not in` either):

```niko
if x is in items:
if not (x is in items):
```

(`--fix` rewrites bare `in`; `not in` is reported for you to rewrite —
the parentheses matter, see §4.9.)

### 3.8 `x = 5` → `set x to 5`

Plain `=` assignment was Python fallthrough. (`--fix` rewrites it;
chained `x = y = 5` is reported without a fix.)

### 3.9 `import os` → Niko imports

Python imports are gone. Niko 2's own module system:

```niko
import "helpers.niko" as h     # another Niko file
use "helpers.niko"             # merge its names into this file
import "pkg:name/main.niko" as p  # an installed package (Alpha 16+)
```

### 3.10 f-strings → `+` and `text()`

```niko
say "n is " + text(n)
```

### 3.11 Hex literals → decimal

`0x1F` doesn't parse; write `31`. (`--fix` converts.)

### 3.12 `//` → `floor(a / b)`

Niko 2 has no floor-division operator. (`--fix` rewrites the simple
shapes.)

### 3.13 Slices → loops with `item N of`

`s[1:3]` doesn't parse. Walk the string explicitly:

```niko
set out to ""
for each i in niko_range(2, 3):
  set out to out + item i of s
```

(Remember: `item N of` is 1-based, §4.7.)

### 3.14 `say "a" "b"` → `say "a" + "b"`

Adjacent string literals concatenated implicitly in Niko 1. Niko 2 wants
the `+` spelled out. (`--fix` inserts it.) `say x, y` (comma-separated)
works in both.

### 3.15 `add [..] to list` → `for each` + `put`

Niko 2's `add` is arithmetic-only (`add 5 to total`). To extend a list:

```niko
for each x in extra:
  put x in list
```

(`put x in list` itself migrates cleanly — it's the same append in both.)

### 3.16 Default arguments → set them in the body

```niko
# Niko 1
to greet with name="world":

# Niko 2
to greet with name:
  if name is nothing:
    set name to "world"
```

Niko 2 parses the header but never binds the default — using the
parameter is an "unknown name" error, which `migrate` reports.

### 3.17 `set d.k to 2` → `set d["k"] to 2`

Niko 2 can't assign through `.` (reading `d.k` is fine — that's new in
Niko 2). (`--fix` rewrites this.)

### 3.18 `join(list)` → `join(list, " ")`

Niko 2's `join` takes the separator as a second argument — and enforces
it at run time, not check time, so this one slips past `niko2 check`.
Niko 1 defaulted the separator to `" "`. (`--fix` adds `" "`.)

### 3.19 Unknown names

`[unknown-name]` is Niko 2's own "unknown name" diagnostic: the name is
a Niko 1-only builtin, a Python fallthrough (`len`, `print`, ...), or a
typo. Niko 2 suggests the nearest builtin ("Did you mean ...?").

### 3.20 Bare `for` → `for each`

```niko
for each x in items:
```

(`--fix` rewrites this.)

### 3.21 `+=` and friends → `add`/`take`/`set`

| Niko 1 (Python) | Niko 2 |
|---|---|
| `x += 5` | `add 5 to x` |
| `x -= 5` | `take 5 from x` |
| `x *= 5` | `set x to x * 5` |
| `x /= 5` | `set x to x / 5` |

(`--fix` rewrites these.)

## 4. Runs but behaves differently

These compile and run on Niko 2 — with different results than Niko 1.
`migrate` reports them as warnings; you decide which matter.

### 4.1 `text()` rendering (VM/WASM)

Niko 1's `text()` used its own `fmt` renderer; Niko 2's VM and WASM
backends use Python's `str()`. (`say` is unaffected — it renders the
Niko 1 way on all backends.)

| expression | Niko 1 | Niko 2 (VM/WASM) |
|---|---|---|
| `text(yes)` | `"yes"` | `"True"` |
| `text(1.0)` | `"1"` | `"1.0"` |
| `text(nothing)` | `"nothing"` | `"None"` |

The native backend matches Niko 1. If you only target native, ignore
this.

### 4.2 `join()` element rendering

Same cause as §4.1: Niko 2's `join` renders elements with `str()`, so
`join([yes], "-")` is `"True"` (Niko 1: `"yes"`) and `join([2.0], "-")`
is `"2.0"` (Niko 1: `"2"`).

### 4.3 `now()` returns a full ISO datetime

Niko 1's `now()` gave `HH:MM:SS`; Niko 2 gives the full ISO datetime
(`today()` is unchanged in spirit — both give the date).

### 4.4 `round()` is banker's rounding

`round(2.5)` is `2` on Niko 2 (Niko 1 rounded half up to `3`).

### 4.5 `split(t, "")` raises

Niko 1 split into characters; Niko 2 raises `ValueError: empty
separator`. Loop over the text with `item N of` instead.

### 4.6 `item 0 of x` wraps instead of raising

Niko 1 raised; Niko 2 wraps to the last element (`item 0 of [10,20,30]`
is `30`).

### 4.7 `x[i]` indexing: lists are 1-based on Niko 2

| | Niko 1 | Niko 2 |
|---|---|---|
| lists | 0-based (`l[0]` is first) | 1-based, wraps (`l[1]` is first, `l[0]` is last) |
| text | 0-based | 0-based (unchanged) |

`d["key"]` (map access) is identical in both. `migrate` warns on every
non-string index — if it's a list index, renumber it.

### 4.8 Comparisons don't chain

Niko 1 chained like Python (`a < b < c`). Niko 2 parses `a is b is c`
as `(a is b) is c`. Split it:

```niko
if a is b and b is c:
```

### 4.9 `not x is in y` parses as `(not x) is in y`

Niko 2 binds `not` tighter than `is in` (and tighter than `and`/`or`/
`is`). Niko 1 read it as `not (x is in y)`. Parenthesize explicitly.
(`--fix` adds the parentheses — semantics preserved.)

### 4.10 Functions can't write top-level variables

Niko 1 functions wrote through to top-level variables:

```niko
set total to 0
to bump with n:
  add n to total   # Niko 1: the global becomes 7
bump(2)
bump(5)
```

On Niko 2, `set` / `ask ... into` / `for each` / `add ... to` /
`take ... from` inside a function always create or update a
*function-local* — the top-level variable keeps its old value (Niko 2
prints `0` above). Restructure: pass the value as a parameter and
`give back` the result, or keep the state in a record you mutate with
`put`/`remove`/`set item` (in-place mutation still reaches the shared
object in both).

## 5. Notes

### 5.1 `and`/`or` — no action needed

Since Alpha 30 these match Niko 1 exactly on all three backends:
short-circuit, returning the deciding operand's value (`say 0 or "hi"`
prints `hi`). `migrate` mentions this once per file so you don't go
checking.

### 5.2 Backend-only rendering notes

Two differences live below the language, in specific backends:
`upper`/`lower` are ASCII-only on WASM, and `text()` matches Niko 1 only
on the native backend (§4.1). If you ship on one backend, test on that
backend.

## 6. What migrates cleanly

A lot, happily: `set`, `say`, `ask` (statement form), `if`/`otherwise
if`/`otherwise`, `while`, `repeat N times`, `for each`, `to`/`give
back`, `stop`/`skip`, `match`, `assert`, `import`/`use` of Niko files,
records and lists, `put x in list`, `remove x from list`, `item N of`
(positive N), `length of`, `numbers A to B`, `random A to B`,
`is`/`is not`/`is in`/long-form comparisons, and the `and`/`or`
semantics (§5.1).

## 7. Worth adopting in Niko 2

While you're migrating, these Niko 2-only features often replace Niko 1
workarounds: `niko2/stdlib/` modules (text, math, lists, records, json),
`pkg:` packages with version pinning, `match` as an expression,
`option`/`result` error handling (`try_read_file`, `unwrap_or`),
`niko2 test` + `assert` for the test suite you always meant to write,
and `niko2 fmt` for consistent formatting.

## 8. The advisor is not a transpiler

`niko2 migrate --fix` handles the mechanical rewrites (§§3.1, 3.3–3.8,
3.11–3.12, 3.14, 3.17–3.18, 3.20–3.21, 4.9). Everything else needs a
human: only you know whether `if name:` meant "non-empty" or "is yes",
or what `use mylib` should become. The intended workflow is iterative —
run `migrate`, fix what's reported, re-run until the errors are gone,
then eyeball the warnings. A file with zero errors *typechecks* on Niko
2; the warnings tell you where to double-check the behavior.
