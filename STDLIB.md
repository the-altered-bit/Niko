# Niko 2 Standard Library

Pure-Niko modules bundled with Niko 2. They behave identically on
the VM, WebAssembly, and native backends. Import one with:

```niko
import "stdlib/text.niko" as text
say text.title("hello world")
```

`import` searches the importing file’s directory first, then each
`NIKO_PATH` directory, then the bundled standard library (for paths
starting with `stdlib/`), then the current working directory. A `stdlib/`
folder next to your file or on `NIKO_PATH` shadows the bundled one.

## stdlib/text — everyday text helpers.

Import with:

```niko
import "stdlib/text.niko" as text
```

### `words(s: text) -> list`

Split on " " and drop the empty pieces.

```niko
text.words("  a  b ")
```
Output: `["a", "b"]`

### `title(s: text) -> text`

Capitalize the first letter of every word and lowercase the rest of each
word. Words are runs of non-space characters; extra spaces are collapsed
to single spaces.

```niko
text.title("hello WORLD")
```
Output: `Hello World`

### `capitalize(s: text) -> text`

Uppercase the first character, leave the rest unchanged. The empty
text stays empty.

```niko
text.capitalize("hello")
```
Output: `Hello`

### `pad_left(s: text, width: number, fill: text) -> text`

Pad on the left with fill until the text is width characters long. Only
the FIRST character of fill is used. If s is already width or longer,
return s unchanged.
Preconditions: fill is not empty; width is a whole number >= 0.

```niko
text.pad_left("42", 5, "0")
```
Output: `00042`

### `pad_right(s: text, width: number, fill: text) -> text`

Pad on the right with fill until the text is width characters long. Only
the FIRST character of fill is used. If s is already width or longer,
return s unchanged.
Preconditions: fill is not empty; width is a whole number >= 0.

```niko
text.pad_right("42", 5, "0")
```
Output: `42000`

### `repeat(s: text, n: number) -> text`

s repeated n times. n <= 0 gives the empty text.
Preconditions: n is a whole number.

```niko
text.repeat("ab", 3)
```
Output: `ababab`

### `slugify(s: text) -> text`

Lowercase; keep a-z and 0-9; space, "_" and "-" become "-"; every other
character is dropped; runs of "-" collapse to one; leading and trailing
"-" are stripped.

```niko
text.slugify("Hello, World!")
```
Output: `hello-world`

### `lines(s: text) -> list`

Split on newline characters.

```niko
text.lines("a\nb")
```
Output: `["a", "b"]`

### `truncate(s: text, n: number) -> text`

If s is longer than n characters, keep the first n characters and add
"..."; otherwise return s unchanged.
Preconditions: n is a whole number >= 0.

```niko
text.truncate("hello world", 5)
```
Output: `hello...`

### `is_blank(s: text) -> boolean`

yes when s is empty or holds only whitespace.

```niko
text.is_blank("   ")
```
Output: `yes`

### `reverse_text(s: text) -> text`

The characters of s in reverse order.

```niko
text.reverse_text("abc")
```
Output: `cba`

### `count_words(s: text) -> number`

The number of space-separated words in s (see words).

```niko
text.count_words("a b c")
```
Output: `3`

## stdlib/math — small math helpers beyond the builtins.

Import with:

```niko
import "stdlib/math.niko" as math
```

### `clamp(x: number, lo: number, hi: number) -> number`

Keep x inside [lo, hi]: values below lo become lo, values above hi
become hi.
Preconditions: lo is at most hi.

```niko
math.clamp(15, 0, 10)
```
Output: `10`

### `lerp(a: number, b: number, t: number) -> number`

Linear interpolation: a + (b - a) * t. t = 0 gives a, t = 1 gives b.

```niko
math.lerp(0, 10, 0.5)
```
Output: `5`

### `gcd(a: number, b: number) -> number`

Greatest common divisor via the Euclidean algorithm, computed on the
absolute values. gcd(0, 0) gives 0.
Preconditions: a and b are whole numbers.

```niko
math.gcd(12, 18)
```
Output: `6`

### `is_prime(n: number) -> boolean`

Trial division up to the square root of n. Numbers below 2 are not prime.
Preconditions: n is a whole number.

```niko
math.is_prime(17)
```
Output: `yes`

### `factorial(n: number) -> number`

n! = 1 * 2 * ... * n, with 0! = 1.
Preconditions: n is a whole number >= 0.

```niko
math.factorial(5)
```
Output: `120`

### `median(xs: list) -> number`

Sort a copy of xs; with an odd count take the middle element, with an
even count average the two middle elements.
Preconditions: xs is not empty and holds numbers.

```niko
math.median([3, 1, 2])
```
Output: `2`

### `sign(x: number) -> number`

-1 when x is negative, 1 when x is positive, 0 when x is zero.

```niko
math.sign(-5)
```
Output: `-1`

### `is_even(n: number) -> boolean`

yes when n is divisible by 2.
Preconditions: n is a whole number.

```niko
math.is_even(4)
```
Output: `yes`

### `is_odd(n: number) -> boolean`

yes when n is not divisible by 2.
Preconditions: n is a whole number.

```niko
math.is_odd(4)
```
Output: `no`

### `digits(n: number) -> list`

The decimal digits of abs(n) as numbers, most significant first.
digits(0) gives [0].
Preconditions: n is a whole number.

```niko
math.digits(402)
```
Output: `[4, 0, 2]`

## stdlib/lists — handy list operations.

Import with:

```niko
import "stdlib/lists.niko" as lists
```

### `chunk(xs: list, n: number) -> list`

Split xs into sub-lists of length n; the last one may be shorter.
Precondition: n >= 1.

```niko
lists.chunk([1, 2, 3, 4, 5], 2)
```
Output: `[[1, 2], [3, 4], [5]]`

### `zip(xs: list, ys: list) -> list`

Pair up items as [x, y] lists, stopping at the shorter input.

```niko
lists.zip([1, 2], ["a", "b", "c"])
```
Output: `[[1, "a"], [2, "b"]]`

### `flatten(xs: list) -> list`

One level of flattening: inner lists are spliced in, non-list
items are kept as-is, and empty inner lists contribute nothing.

```niko
lists.flatten([[1, 2], [3], 4])
```
Output: `[1, 2, 3, 4]`

### `take(xs: list, n: number) -> list`

The first n items of xs, or fewer when xs is shorter than n.

```niko
lists.take([1, 2, 3], 2)
```
Output: `[1, 2]`

### `drop(xs: list, n: number) -> list`

Everything after the first n items of xs.

```niko
lists.drop([1, 2, 3], 2)
```
Output: `[3]`

### `pluck(xs: list, key: text) -> list`

The value at key for each record in xs.
Precondition: every item of xs is a record holding key.

```niko
lists.pluck([{name: "a"}, {name: "b"}], "name")
```
Output: `["a", "b"]`

### `group_by(xs: list, key_fn: function) -> map`

Group items by text(key_fn(item)). Each key maps to its items in
first-seen order; keys follow first-seen order too.

```niko
to first_letter with s:
    give back item_of(1, s)
lists.group_by(["apple", "avocado", "kiwi"], first_letter)
```
Output: `{a: ["apple", "avocado"], k: ["kiwi"]}`

### `partition(xs: list, pred: function) -> list`

[matches, non_matches]: items passing pred first, then the rest,
each in original order.

```niko
to is_even with n:
    give back n % 2 is 0
lists.partition([1, 2, 3, 4], is_even)
```
Output: `[[2, 4], [1, 3]]`

### `frequencies(xs: list) -> map`

Count occurrences: text(item) maps to how many times it appears.

```niko
lists.frequencies(["a", "b", "a"])
```
Output: `{a: 2, b: 1}`

## stdlib/records — small record (map) helpers.

Import with:

```niko
import "stdlib/records.niko" as records
```

### `record_keys(r: map) -> list`

All keys of r. A tiny helper so omit can keep its `keys` parameter
name without shadowing the keys() builtin inside its own body.

```niko
records.record_keys({a: 1})
```
Output: `["a"]`

### `merge(a: map, b: map) -> map`

All keys of a plus all keys of b; b wins on conflicts.
Neither input is mutated.

```niko
records.merge({x: 1}, {x: 9, y: 2})
```
Output: `{x: 9, y: 2}`

### `pick_keys(r: map, keys: list) -> map`

A new record holding only the listed keys that exist in r.

```niko
records.pick_keys({a: 1, b: 2}, ["a", "c"])
```
Output: `{a: 1}`

### `omit(r: map, keys: list) -> map`

A new record like r but without the listed keys.

```niko
records.omit({a: 1, b: 2}, ["b"])
```
Output: `{a: 1}`

### `get_or(r: map, key: text, default: any) -> any`

The value at r[key] when key exists, otherwise default.

```niko
records.get_or({a: 1}, "b", 0)
```
Output: `0`

### `invert(r: map) -> map`

Flip entries: text(value) maps back to its key.
Precondition: values are distinct (later keys win on clashes).

```niko
records.invert({a: "x", b: "y"})
```
Output: `{x: "a", y: "b"}`

### `map_values(r: map, f: function) -> map`

A new record with f applied to each value; keys are unchanged.

```niko
to double with n:
    give back n * 2
records.map_values({a: 1, b: 2}, double)
```
Output: `{a: 2, b: 4}`

## stdlib/json — JSON parsing and serialization.

Import with:

```niko
import "stdlib/json.niko" as json
```

parse turns JSON text into Niko values: objects become records, arrays
become lists, and true/false/null become yes/no/nothing. stringify goes
the other way, writing compact JSON with record keys in insertion order
(the order parse produces).

Known limitations:
- \uXXXX escapes above U+007F are not supported by parse; they are a

```niko
parse error naming the character position. Write such characters as
raw UTF-8 instead (JSON allows it); raw multibyte UTF-8 parses on
all three backends. stringify of multibyte text works on all three
backends too.
```

- Numbers with magnitude >= 1e15 are rejected with "number out of

```niko
range": beyond that the three backends cannot agree (the VM keeps
integers exact while WASM and native use f64, which would silently
round or fail). Carry bigger integers as text.
```

- Integer-valued floats render as "1.0" on the VM but "1" on WASM and

```niko
native (each backend's own text() rule); both are valid JSON for the
same value.
```

- stringify of any other value (for example a function) is an error.

### `parse(text) -> any`

Parse one JSON document. Objects become records (duplicate keys: the
last value wins, the first position is kept), arrays become lists,
strings become text, numbers become numbers, and true/false/null become
yes/no/nothing. Leading and trailing whitespace is allowed; anything
else is a parse error naming the 1-based character position, for
example "json parse error at character 12: unexpected end of input".

```niko
json.parse("{\"name\": \"Ada\", \"n\": 1.5}")
```
Output: `{name: "Ada", n: 1.5}`

```niko
json.parse("[1, \"two\", true, null]")
```
Output: `[1, "two", yes, nothing]`

```niko
json.parse("{\"a\": 1, \"a\": 2}")
```
Output: `{a: 2}`

### `stringify(value) -> text`

Render a Niko value as compact JSON text: records as {"k":v} with keys
in insertion order, lists as [v,v], text quoted and escaped, numbers
via text(), yes/no/nothing as true/false/null. Any other value (for
example a function) is a "json stringify error".
Booleans are detected by their text() rendering ("True"/"yes" and
"False"/"no") rather than `value is yes`, because Niko `is` is equality:
`1 is yes` and `0 is no` are both `yes`, so a bare `is yes` test would
stringify the number 1 as `true`. The text check runs first so the
*string* "True" is still quoted rather than mistaken for a boolean.

```niko
json.stringify({b: 1, a: [yes, nothing]})
```
Output: `{"b":1,"a":[true,null]}`

```niko
json.stringify("hi")
```
Output: `"hi"`

```niko
json.stringify(1.5)
```
Output: `1.5`

```niko
json.stringify(nothing)
```
Output: `null`

### `_err_text(pos, msg) -> text`

The full message for a parse failure at 1-based character pos.

```niko
json._err_text(3, "unexpected end of input")
```
Output: `json parse error at character 3: unexpected end of input`

### `_is_ws(c) -> boolean`

yes for the four JSON whitespace characters: space, tab, newline and
carriage return.

```niko
json._is_ws(" ")
```
Output: `yes`

```niko
json._is_ws("\t")
```
Output: `yes`

```niko
json._is_ws("a")
```
Output: `no`

### `_char_at(s, pos) -> text`

The character at 1-based position pos in s, or "" when pos is past the
end. Unlike item_of, it never fails; this lets short-circuit and/or
conditions use it safely on every backend. (The native backend
evaluates a condition's call arguments before testing the first
operand, so a guarded item_of would still panic there.)

```niko
json._char_at("hi", 1)
```
Output: `h`

```niko
json._char_at("hi", 9) is ""
```
Output: `yes`

### `_is_digit(c) -> boolean`

yes for a single ASCII digit. Unlike has("0123456789", c), this is no
for "" (the empty string counts as "found" for has), so it is safe to
use with _char_at at the end of input.

```niko
json._is_digit("5")
```
Output: `yes`

```niko
json._is_digit("")
```
Output: `no`

```niko
json._is_digit("x")
```
Output: `no`

### `_skip_ws(s, cur) -> nothing`

Advance cur["pos"] past any JSON whitespace.

```niko
set cur to {pos: 1}
json._skip_ws("  x", cur)
cur["pos"]
```
Output: `3`

### `_expect_word(s, cur, word) -> nothing`

Consume the exact word at cur["pos"]; a mismatch or the end of input
is a parse error naming the character position.

```niko
set cur to {pos: 1}
json._expect_word("true!", cur, "true")
cur["pos"]
```
Output: `5`

### `_parse_literal(s, cur) -> any`

Parse true/false/null at cur["pos"]; the t/f/n dispatch already ran in
_parse_value.

```niko
json._parse_literal("true", {pos: 1})
```
Output: `yes`

```niko
json._parse_literal("null ", {pos: 1})
```
Output: `nothing`

### `_parse_value(s, cur) -> any`

Parse one JSON value at cur["pos"]; leave cur["pos"] just past it.

```niko
json._parse_value("[1, 2]", {pos: 1})
```
Output: `[1, 2]`

```niko
json._parse_value("{\"x\": null}", {pos: 1})
```
Output: `{x: nothing}`

### `_parse_object(s, cur) -> map`

Parse a {...} object starting at cur["pos"] (which holds the {).
Keys keep first-seen order; a repeated key keeps its position but takes
the last value.

```niko
json._parse_object("{\"b\": 1, \"a\": 2}", {pos: 1})
```
Output: `{b: 1, a: 2}`

```niko
json._parse_object("{}", {pos: 1})
```
Output: `{}`

### `_parse_array(s, cur) -> list`

Parse a [...] array starting at cur["pos"] (which holds the [).

```niko
json._parse_array("[1, \"a\", null]", {pos: 1})
```
Output: `[1, "a", nothing]`

```niko
json._parse_array("[]", {pos: 1})
```
Output: `[]`

### `_parse_string(s, cur) -> text`

Parse a "..." string starting at cur["pos"] (which holds the opening
quote); all JSON escapes are decoded, see _parse_escape.

```niko
json._parse_string("\"hi\"", {pos: 1})
```
Output: `hi`

```niko
length(json._parse_string("\"a\\nb\"", {pos: 1}))
```
Output: `3`

```niko
json._parse_string("\"a\\u0041b\"", {pos: 1})
```
Output: `aAb`

### `_parse_escape(s, cur) -> text`

Decode the JSON escape at cur["pos"] (the character after the
backslash); leaves cur["pos"] just past the escape. \uXXXX escapes go
through _parse_unicode.

```niko
json._parse_escape("\"x", {pos: 1})
```
Output: `"`

```niko
length(json._parse_escape("nx", {pos: 1}))
```
Output: `1`

```niko
json._parse_escape("u0041x", {pos: 1})
```
Output: `A`

### `_parse_unicode(s, cur) -> text`

Decode the four hex digits at cur["pos"] (the \u was already consumed);
leaves cur["pos"] just past the digits. Only U+0000..U+007F (ASCII) are
supported; anything higher is a parse error naming the position of the
\u. (\u0080 and above are rejected on every backend to stay
byte-identical; write such characters as raw UTF-8 instead.)

```niko
json._parse_unicode("0041\"", {pos: 1})
```
Output: `A`

```niko
json._parse_unicode("000a\"", {pos: 1}) is "\n"
```
Output: `yes`

### `_hex_val(c) -> number`

Value of the hex digit c (case-insensitive), or -1 for anything else.

```niko
json._hex_val("e")
```
Output: `14`

```niko
json._hex_val("9")
```
Output: `9`

```niko
json._hex_val("q")
```
Output: `-1`

### `_u_table() -> text`

The 128 ASCII characters U+0000..U+007F in order, so item_of(code + 1,
_u_table()) is the character for a \u00XX escape with XX <= 7F.

```niko
length(json._u_table())
```
Output: `128`

```niko
item_of(66, json._u_table())
```
Output: `A`

### `_decimal_exp(int_d, frac_d, exp_val) -> any`

The decimal exponent E with 10^E <= |value| < 10^(E+1) for the number
being scanned (int_d/frac_d are its digit runs, exp_val its exponent),
or nothing when the value is zero. Used to reject magnitudes >= 1e15,
which the three backends cannot represent the same way.

```niko
json._decimal_exp("1", "", 21)
```
Output: `21`

```niko
json._decimal_exp("0", "05", 0)
```
Output: `-2`

```niko
json._decimal_exp("0", "00", 0)
```
Output: `nothing`

### `_parse_number(s, cur) -> number`

Parse a JSON number at cur["pos"] (leading - or digit). The grammar
-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)? is validated by hand;
then the exact slice goes through try_number. Magnitudes >= 1e15 are a
"number out of range" parse error.

```niko
json._parse_number("42,", {pos: 1})
```
Output: `42`

```niko
json._parse_number("3.25]", {pos: 1})
```
Output: `3.25`

```niko
set cur to {pos: 1}
json._parse_number("42,", cur)
cur["pos"]
```
Output: `3`

### `_escape_char(c) -> text`

The JSON escape for one character: \" \\ \n \t \r \b \f, \u00XX for
other codepoints below U+0020, and c itself otherwise.

```niko
json._escape_char("\"")
```
Output: `\"`

```niko
json._escape_char("\\")
```
Output: `\\`

```niko
json._escape_char("a")
```
Output: `a`

### `_stringify_string(s) -> text`

Quote s for JSON, escaping through _escape_char per character.

```niko
json._stringify_string("a\"b")
```
Output: `"a\"b"`

```niko
json._stringify_string("hi")
```
Output: `"hi"`

### `_stringify_list(items) -> text`

The inside of a JSON array: elements joined with "," (no brackets).

```niko
json._stringify_list([1, "a"])
```
Output: `1,"a"`

```niko
json._stringify_list([1, "a", nothing])
```
Output: `1,"a",null`

### `_stringify_record(rec) -> text`

The inside of a JSON object: "key":value pairs in insertion order,
joined with "," (no braces).

```niko
json._stringify_record({b: 1})
```
Output: `"b":1`

```niko
json._stringify_record({a: 1, b: "x"})
```
Output: `"a":1,"b":"x"`

### `_stringify_number(value) -> text`

Render a number with text(); anything that is not a finite number is
a "json stringify error" (functions have no JSON form, and neither do
infinite or NaN values).

```niko
json._stringify_number(42)
```
Output: `42`

```niko
json._stringify_number(1.5)
```
Output: `1.5`

```niko
json._stringify_number(-0.5)
```
Output: `-0.5`
