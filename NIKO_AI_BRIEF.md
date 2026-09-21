# Niko: briefing for an AI (learning it, teaching it, or developing it)

Paste this whole file at the start of a conversation. If you can, also attach `niko.py`
(the reference implementation, about 500 lines). Then say what you want, for example:
"Teach me Niko with 5 small exercises" or "Add a `sort` helper to both engines and add a test".

---

## 1. What Niko is

Niko is a general-purpose, beginner-friendly programming language whose programs read like
English instructions. Files end in `.niko`. It is dynamically typed, uses indentation for blocks
(like Python), and has automatic memory management because it runs on top of a host language.

There are two engines that must always behave the same:

| Engine | File | Translates Niko to | Where it runs |
|---|---|---|---|
| Desktop | `niko.py` | Python | Any computer with Python 3.8+ (also has a REPL and `use`) |
| Browser IDE | `ide/niko-ide.html` | JavaScript | Any browser, including phones (editor, Run, REPL tab) |

Design goals: readable minimal syntax; consistent rules; clear error messages (what and where);
dynamic typing; automatic memory management; interactive REPL; rich standard library; strong docs
and community; gentle learning curve (something useful in the first few lines).

## 2. How to behave (instructions to the AI)

- When **teaching**: use only the features in this file, keep examples short, explain errors in plain
  words, give one small exercise at a time. Never invent syntax. If unsure a feature exists, say so.
- When **writing Niko**: 4-space indentation, one instruction per line, descriptive `lower_case` names,
  `text(x)` when joining a number to text, positions start at 1.
- When **developing the language**: change both engines, keep line numbers stable, add a test case
  in `tests/cases/`, run `python tests/run_tests.py --ide`, and update the guide's A to Z reference.
  Follow section 7.

## 3. The language in one page

Program shape: lines of text. A line ending in `:` opens a block; the block is the following lines
indented deeper. Comments start with `#`. Text uses `"double"` or `'single'` quotes. Names are letters,
digits and underscores. Everything is case-sensitive; keywords are lowercase.

```
# statements
set NAME to VALUE                    create or change a variable
set item N of LIST to VALUE          change a list position (N starts at 1)
set RECORD["key"] to VALUE           change or add a record label
add VALUE to NAME                    NAME += VALUE
take VALUE from NAME                 NAME -= VALUE
put VALUE in LIST                    append
remove VALUE from LIST               remove the first match (no error if missing)
say A, B, C                          print values separated by spaces (no arguments prints an empty line)
ask TEXT into NAME                   read text
ask number TEXT into NAME            read a number, asking again until valid
if COND:  /  otherwise if COND:  /  otherwise:
repeat N times:      repeat forever:
for each NAME in LIST_OR_TEXT:       while COND:
stop                                 leave the loop
skip                                 next round
to NAME with A, B:   /   to NAME:    define a function (call it as NAME(x, y) or NAME())
give back VALUE                      return
match VALUE:  /  when PATTERN:  /  otherwise:     (Niko 2 only) pick a branch by shape — see 3b
use MODULE                           import a Python library (desktop only)
ANYTHING ELSE                        an expression, e.g. a function call: greet("Sam")
```

Expressions:

```
math          + - * / % **  and parentheses         (no // ; use floor(a / b))
comparison    is   is not   is bigger than   is smaller than   is at least   is at most   is in
logic         and   or   not          values: yes  no  nothing
lists         [1, 2, 3]      item N of X      length of X      numbers A to B (inclusive, counts down if A > B)
records       {"name": "Niko", "age": 2}      record["name"]
random        random A to B (whole number, inclusive)
```

`X` after `item N of`, `length of`, and `is in` is a name, optionally followed by one call or one index:
`length of read_file("a.txt")`, `item 2 of pet["tags"]`. Anything more complex: store it in a variable first.

Text: join with `+`. To join a number use `text(n)` (the desktop engine refuses `"a" + 5`).

Scope: a function can read and change variables created at the top level (by `set`, `ask ... into`,
or `for each`). A variable first created inside a function is local to it. Parameters are local.

Output formatting: `yes`/`no`/`nothing`; whole floats print without `.0` (`10 / 2` prints `5`);
lists print like `[1, "two", yes]` (strings quoted inside lists); records print like `{name: "Niko"}`.

## 4. Standard library (built in, no import)

| Group | Helpers |
|---|---|
| Numbers | `abs(x)` `ceil(x)` `floor(x)` `round(x, digits)` `sqrt(x)` `max(...)` `min(...)` `pi` `random A to B` |
| Text | `upper(t)` `lower(t)` `trim(t)` `replace(t, a, b)` `starts_with(t, p)` `ends_with(t, p)` `split(t, sep)` `join(list, sep)` `count_of(t, part)` `text(x)` `number(t)` |
| Lists | `sorted(l)` `reversed(l)` `unique(l)` `sum(l)` `average(l)` `max(l)` `min(l)` `pick(l)` `count_of(l, x)` `has(c, x)` (same as `x is in c`) |
| Records | `keys(record)` |
| Files | `write_file(name, text)` `append_file(name, text)` `read_file(name)` `read_lines(name)` `file_exists(name)` |
| Time | `today()` `now()` `sleep(seconds)` |
| Results (Niko 2 only) | `ok(v)` `error(msg)` `is_ok(r)` `is_error(r)` `unwrap(r)` `unwrap_or(r, default)` `error_message(r)` `try_read_file(name)` `try_number(text)` |

### 3b. `match` and results (Niko 2 only)

Some things can fail — reading a file that isn't there, turning text into
a number. Instead of crashing, Niko 2 can hand you a **result**: `ok(value)`
when it worked, `error(message)` when it didn't. `try_read_file(name)` and
`try_number(text)` return results; the plain `read_file` / `number` still
raise the friendly error as before.

```
match try_number(ask "age?"):
    when ok n:
        say "you are " + text(n)
    when error msg:
        say "that wasn't a number"
```

Patterns, tried top to bottom, first match wins: a literal (`1`, `"quit"`,
`yes`, `nothing`), `ok name` / `error name` (the name holds the value /
the message), a bare name (`when d:` catches anything and names it), or
several patterns with commas (`when "sat", "sun":`). Patterns can also
take lists apart — `when [a, b]:` (exactly two items), `when []:` (empty
only), `when [first, ...rest]:` (head and tail) — and records —
`when {name: n, age: a}:` (needs those keys; extra keys are fine) — nested
as deeply as you like (`when [[a], {x: b}]:`). An arm can carry a guard:
`when [a] if a is bigger than 10:` runs only when the pattern matches
*and* the guard is true (the guard can use the pattern's names); a failed
guard just tries the next arm. `otherwise:` is the
optional fallback; with no match and no `otherwise`, the program just
carries on. An **option** (`option<T>`) is even simpler: a value or
`nothing` — `unwrap_or(maybe_name, 0)` gives the value or `0`.

`match` also works as an expression — the winning arm's last line is the
value:

```
set grade to match score:
    when n if n is at least 90:
        "honors"
    when n if n is at least 50:
        "pass"
    otherwise:
        "fail"
```

Each arm must end with an expression (not `say`), all arms must produce
the same kind of value, and the match must be exhaustive — it needs
`otherwise:` or a catch-all `when x:` as the last arm. Also usable after
`give back` and `say`.

In the browser IDE, files live in `localStorage` (virtual); on the desktop they are real files.

## 5. Error messages (part of the language design)

Format: `Oops on line N: MESSAGE`. Messages are plain English. Examples:
`I don't know what "totl" is. Did you mean "total"?` (edit distance <= 2 against your names and helper names);
`That position isn't in the list (positions start at 1).`; `I couldn't find the file "x".`;
`I can't turn "abc" into a number.`; `"otherwise" needs an "if" right above it`;
`You mixed text and numbers. Use text(...) to turn a number into text.` (desktop).

## 6. Known differences between the engines (candidates to fix)

1. Division by zero: desktop raises an error, browser gives infinity.
2. `"a" + 5`: desktop error, browser silently gives `"a5"`. Recommend `text()` always.
3. Truthiness of empty lists and text as conditions differs (Python vs JavaScript). Compare explicitly.
4. Very large integers: Python is exact, JavaScript uses doubles (`factorial(25)` differs).
5. Record keys that look like integers keep insertion order on desktop but are sorted in the browser.
6. `use` exists only on the desktop.

## 7. How the engines work (for developers)

**Both engines translate line by line and keep line numbers identical** (blank lines stay blank), so
runtime errors can name the Niko line.

`niko.py`
- `words(expr)`: hides quoted text behind placeholders `__qN__`, applies regex rewrites
  (`is in`, `is not`, `is bigger than`, ..., `length of`, `item N of`, `numbers A to B`, `random A to B`), restores text.
- `translate(line, ctx)`: one regex per statement form (order matters: `set item` before `set`, `ask number` before `ask`,
  `otherwise if` before `otherwise`). Returns a Python line.
- `transpile(src, globals_, names)`: first pass finds function parameters, top-level variable names (the "globals"),
  and all names (for did-you-mean). Inside a function, assignments to a global use `globals()["x"] = ...`.
  Checks that `otherwise` follows an `if`.
- `LIB` dict: all helpers plus `yes`/`no`/`nothing`. `HELPER_NAMES`: used for suggestions.
- `run()` executes with friendly errors; `repl()` keeps one environment between lines.

`ide/niko-ide.html` (single file, no dependencies)
- Region between `/*<TRANSPILER>*/` and `/*</TRANSPILER>*/` is the engine (also loaded by `tests/ide_harness.js`).
  It has the same `words` / `translate` / `transpile` structure, but emits JavaScript: block braces come from an
  indentation stack, `var` for locals, `await` is inserted before calls to user functions, every statement is
  prefixed with `__st.l=N;` (current line for errors), loops call `await __tick()` (keeps the page responsive and makes
  Stop work). `diagnose()` compiles the output and bisects to the first bad line for the Problems panel.
- `LIB` (helpers), `ENV` (names passed into the generated function), `HELPERS` (suggestions).
- REPL tab: statements run inside `with(proxy)` so variables persist; functions are emitted as `name = async function(...)`.
- UI: editor is a transparent `<textarea>` over a highlighted `<pre>`; autocomplete, palette (Ctrl+Shift+P), files in localStorage.

**Checklist: add a helper** (example: `title(t)`)
1. `niko.py`: add to `LIB` and `HELPER_NAMES`.
2. IDE: add to `LIB`, `ENV`, `HELPERS`, the highlight `BUILT` set, `SNIP` list, and the `REF` sheet.
3. Guide `docs/niko-guide.html`: add an A to Z entry (and an example if useful).
4. Add `tests/cases/NAME.niko` (+ `.in` if it asks) and `.out`; run `python tests/run_tests.py --ide`.

**Checklist: add a statement**: add a regex to `translate` in both engines (mind ordering), decide how it is
highlighted (`STMT`/`CTRL` sets in the IDE), add tests, update the guide's "language rules" chapter.

## 8. Running things

```
python niko.py examples/hello.niko           run a program
python niko.py examples/hello.niko --show    print the generated Python
python niko.py                               open the REPL
python tests/run_tests.py --ide              all test cases, both engines (needs node for --ide)
```

## 9. Roadmap ideas (pick one at a time)

1. Optional type notes, e.g. `set age to 12 as number`, checked at run time with friendly errors.
2. Splitting programs into files: `use "helpers.niko"`.
3. Records with functions, or a simple `thing` (class) syntax in words.
4. More helpers: sorting by a key, filtering, `index_of`, string formatting, JSON, dates.
5. Column numbers and a caret in syntax errors; a "why" line for common mistakes.
6. Remove the engine differences in section 6 (strict `+`, division check, explicit truthiness rules).
7. A pip package (`pip install niko`, `niko file.niko`) and a VS Code extension (highlighting rules are in the IDE's `highlight()`).
8. A written grammar plus a conformance suite that any new engine must pass.

## 10. Example programs

```
ask number "Degrees Celsius? " into c
say c, "C is", c * 9 / 5 + 32, "F"
```

```
to factorial with n:
    if n is at most 1:
        give back 1
    give back n * factorial(n - 1)

say factorial(5)
```

```
set counts to {}
for each word in split("the cat and the hat", " "):
    if word is in counts:
        set counts[word] to counts[word] + 1
    otherwise:
        set counts[word] to 1
say counts
```

## 11. Brand

Mascot: a geometric, low-poly owl in blue and purple with yellow eyes. Files are in `assets/`:
`logo.png` (full logo, transparent, made for dark backgrounds: white "niko" plus cyan ".niko"),
`niko-owl.png` and `niko-owl-{512,360,180,64,32}.png` (owl only, works on light and dark).
Palette: cobalt `#226edf`, deep navy `#011466`, cyan `#48d6f4`, violet `#2f21b3` to `#7a3cff`, eye yellow `#ffc107`.
`social-preview.png` is the 1280x640 card for GitHub's social preview.
On light backgrounds use the owl-only files, because the wordmark in `logo.png` is white.
