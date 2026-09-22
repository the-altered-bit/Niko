"""Semantic versions and version ranges (Alpha 19).

Versions are strict ``X.Y.Z`` -- the same rule package manifests use:
non-negative integers, no leading zeros (``1.2.3`` ok, ``01.2.3`` no).

Range grammar (deliberately small, npm-inspired)::

    *                any version
    1.2.3            exactly 1.2.3
    1.2              1.2.x  (>=1.2.0, <1.3.0);  1  means 1.x
    ^1.2.3           compatible with 1.2.3 (caret -- see note below)
    ^1.2             caret allows partial versions too: ^1.2 means ^1.2.0
    ~1.2.3           same minor: >=1.2.3, <1.3.0  (~1.2 means ~1.2.0)
    >=1.0  >1.0  <=2.0  <2.0   comparators; the operand may be X, X.Y or X.Y.Z
    =1.2.3  ==1.2.3    exact (full X.Y.Z only)
    >=1.0, <2.0       comma-separated clauses are ANDed together

Caret rule (follows npm): the leftmost non-zero *specified* component
is pinned; missing components are treated as zero for the lower bound::

    ^1.2.3  ->  >=1.2.3, <2.0.0     (major pinned)
    ^1.2    ->  >=1.2.0, <2.0.0     (partial = ^1.2.0)
    ^0.2.3  ->  >=0.2.3, <0.3.0     (minor pinned while major is 0)
    ^0.2    ->  >=0.2.0, <0.3.0
    ^0.0.3  ->  >=0.0.3, <0.0.4     (patch pinned while major+minor are 0)
    ^0.0    ->  >=0.0.0, <0.1.0
    ^0      ->  >=0.0.0, <1.0.0     (all specified components zero: the
                                    last specified one is pinned)

Rationale: for 0.x releases every minor bump (and for 0.0.x every patch
bump) is allowed to break compatibility, so caret stays conservative
there instead of jumping a whole major/minor. Tilde pins the minor
(``~1`` pins the major, like a bare ``1``).

The solver is ``max_satisfying``: given candidate version strings and a
range string, return the highest version satisfying the range, or None
when nothing matches. All failures raise ``SemverError`` with
plain-English text (callers surface it to the user as-is).

Stdlib only, no intra-package imports -- safe to import from anywhere.
"""

import re

#: Strict X.Y.Z -- same shape as manifests enforce.
_VERSION_RE = re.compile(r'^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$')
#: X, X.Y or X.Y.Z for comparator operands.
_PARTIAL_RE = re.compile(r'^(0|[1-9]\d*)(?:\.(0|[1-9]\d*))?(?:\.(0|[1-9]\d*))?$')
_OPS = ('>=', '<=', '>', '<', '==', '=')


class SemverError(ValueError):
    """A version or range that doesn't parse, already worded for humans."""


def parse_version(text):
    """``"1.2.3"`` -> ``(1, 2, 3)``. Strict; raises ``SemverError``."""
    s = (text or '').strip()
    m = _VERSION_RE.match(s)
    if not m:
        raise SemverError(
            f'bad version "{s}" -- use strict X.Y.Z, e.g. "1.2.3"')
    return tuple(int(g) for g in m.groups())


def _parse_partial(text, what):
    m = _PARTIAL_RE.match((text or '').strip())
    if not m:
        raise SemverError(
            f'bad version "{text}" in {what} -- use X, X.Y or X.Y.Z, e.g. "2", "1.2", "1.2.3"')
    parts = [int(g) for g in m.groups() if g is not None]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def _fmt(v):
    return '.'.join(str(n) for n in v)


class VersionRange:
    """A parsed range: a list of ``(op, version_tuple)`` clauses, ANDed."""

    def __init__(self, clauses, text):
        self.clauses = list(clauses)
        self.text = text

    def matches(self, version):
        """Does version tuple *version* satisfy every clause?"""
        for op, ref in self.clauses:
            if op in ('=', '=='):
                if version != ref:
                    return False
            elif op == '>=':
                if version < ref:
                    return False
            elif op == '>':
                if version <= ref:
                    return False
            elif op == '<=':
                if version > ref:
                    return False
            elif op == '<':
                if version >= ref:
                    return False
            else:  # pragma: no cover -- constructed internally only
                raise SemverError(f'internal error: unknown operator "{op}"')
        return True

    def __str__(self):
        return self.text

    def __repr__(self):  # pragma: no cover -- debugging aid
        return f'VersionRange({self.text!r})'


def _caret_clauses(v, n_given):
    """Desugar ``^v`` per the caret rule documented above.

    *n_given* is how many components were written (``^1.2`` -> 2):
    the bump lands on the leftmost non-zero specified component, or on
    the last specified one when they are all zero.
    """
    bump = n_given - 1
    for i in range(n_given):
        if v[i] != 0:
            bump = i
            break
    upper = list(v)
    upper[bump] += 1
    for j in range(bump + 1, 3):
        upper[j] = 0
    return [('>=', v), ('<', tuple(upper))]


def _tilde_clauses(v, n_given):
    """``~1.2.3`` -> ``>=1.2.3, <1.3.0`` (same minor; ``~1`` -> ``1.x``)."""
    if n_given == 1:
        return [('>=', v), ('<', (v[0] + 1, 0, 0))]
    return [('>=', v), ('<', (v[0], v[1] + 1, 0))]


def _bare_partial_clauses(v, n_given):
    """Bare ``1`` / ``1.2`` behave like npm's ``1.x`` / ``1.2.x``."""
    if n_given == 1:
        return [('>=', v), ('<', (v[0] + 1, 0, 0))]
    return [('>=', v), ('<', (v[0], v[1] + 1, 0))]


def parse_range(text):
    """Parse a range string into a ``VersionRange``.

    ``*`` (or an empty string) matches everything. Raises
    ``SemverError`` naming the offending clause.
    """
    s = (text or '').strip()
    if not s or s == '*':
        return VersionRange([], s or '*')
    clauses = []
    for raw_clause in s.split(','):
        clause = raw_clause.strip()
        if not clause:
            raise SemverError(
                f'bad version range "{s}": empty clause -- '
                'clauses look like ">=1.0", "<2.0", "^1.2.3"')
        if clause == '*':
            continue  # "*" inside a conjunction adds nothing
        op = None
        rest = clause
        for candidate in _OPS:
            if clause.startswith(candidate):
                op = candidate
                rest = clause[len(candidate):].strip()
                break
        if op is None:
            if clause[0] in '^~':
                op, rest = clause[0], clause[1:].strip()
            else:
                op, rest = 'bare', clause
        if op == '^' or op == '~':
            # Caret/tilde accept partial versions, padded with zeros
            # (^1.2 means ^1.2.0); the bump rule sees how many
            # components were actually written.
            m = _PARTIAL_RE.match(rest)
            if not m:
                raise SemverError(
                    f'bad version range "{s}": "{clause}" needs a version '
                    'after ^ or ~, e.g. "^1.2.3" or "^1.2"')
            n_given = sum(1 for g in m.groups() if g is not None)
            v = _parse_partial(rest, f'range "{s}"')
            clauses.extend(_caret_clauses(v, n_given) if op == '^'
                           else _tilde_clauses(v, n_given))
        elif op == 'bare':
            m = _PARTIAL_RE.match(rest)
            if not m:
                raise SemverError(
                    f'bad version range "{s}": "{clause}" is not a version -- '
                    'try "1.2.3", "^1.2.3", ">=1.0, <2.0", or "*"')
            given = [g for g in m.groups() if g is not None]
            v = _parse_partial(rest, f'range "{s}"')
            if len(given) == 3:
                clauses.append(('==', v))
            else:
                clauses.extend(_bare_partial_clauses(v, len(given)))
        else:
            v = _parse_partial(rest, f'range "{s}"')
            if op in ('=', '==') and rest.count('.') < 2:
                # "=1" / "=1.2" behave like the bare partials above.
                clauses.extend(_bare_partial_clauses(v, rest.count('.') + 1))
            else:
                clauses.append((op, v))
    return VersionRange(clauses, s)


def satisfies(version, range_text):
    """Does version string *version* satisfy range string *range_text*?"""
    return parse_range(range_text).matches(parse_version(version))


def max_satisfying(versions, range_text):
    """Highest version in *versions* satisfying *range_text*, or None.

    *versions* are strings; unparseable entries are skipped (a registry
    index with a garbage key shouldn't take down the whole solve -- the
    index loader validates keys strictly when it matters).
    """
    rng = parse_range(range_text)
    best = None
    best_key = None
    for v in versions:
        try:
            key = parse_version(v)
        except SemverError:
            continue
        if rng.matches(key) and (best_key is None or key > best_key):
            best, best_key = v.strip(), key
    return best
