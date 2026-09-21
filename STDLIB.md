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
