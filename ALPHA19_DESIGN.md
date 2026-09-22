# Alpha 19 design: package registry + version-range solving

## Goal

Alpha 16 gave Niko 2 a package manager with no registry and no version
ranges: `niko2 get` installed exactly the version described by the
source's own `niko.toml`. Alpha 19 closes that gap: publish versioned
packages to a **registry** (a local directory, or a remote index served
over HTTP), then install with `niko2 get <name>` / `niko2 get
<name>@<range>` and let the solver pick the newest version satisfying
the range. Dependencies (`[dependencies]` in `niko.toml`) install as a
transitive closure. Deliberately **no auth, no remote publish, no
version in the import string** — the registry is a dumb index +
tarballs; `niko.lock` pins stay the reproducibility story.

## Design

### Version ranges (`niko2/semver.py`)

Versions are strict `X.Y.Z` — the same rule manifests enforce. The
range grammar is deliberately small and npm-inspired:

```
*                any version (empty string too)
1.2.3            exactly 1.2.3
1.2              1.2.x (>=1.2.0, <1.3.0);  1 means 1.x
^1.2.3           caret: leftmost non-zero *specified* component pinned
                 (^1.2.3 -> >=1.2.3, <2.0.0;  ^0.2.3 -> >=0.2.3, <0.3.0;
                 ^0.0.3 -> >=0.0.3, <0.0.4;  ^0 -> >=0.0.0, <1.0.0)
~1.2.3           same minor: >=1.2.3, <1.3.0  (~1 pins the major, like bare 1)
>=1.0  >1.0  <=2.0  <2.0   comparators (operand may be X, X.Y or X.Y.Z)
=1.2.3  ==1.2.3    exact (full X.Y.Z; =1.2 behaves like bare 1.2)
>=1.0, <2.0       comma-separated clauses are ANDed
```

The solver is `max_satisfying(versions, range) -> str | None`: the
highest listed version satisfying the range, or `None`. All parse
failures raise `SemverError` (a `ValueError`) with plain-English text;
callers surface it as-is. The module is stdlib-only and import-safe
from anywhere.

### Registry index format

A registry is an index document plus the tarballs it points at:

```
<registry>/
    index.json
    tarballs/<name>-<version>.tar.gz
```

```json
{
  "packages": {
    "<name>": {
      "<version>": {
        "url": "tarballs/<name>-<version>.tar.gz",
        "sha256": "<hex sha256 of the tarball>",
        "description": "...",
        "dependencies": {"<name>": "<range>"}
      }
    }
  }
}
```

`url` may be relative (resolved against the registry: the directory for
a local registry, the index URL's directory for a remote one) or an
absolute `http(s)://` / `file://` URL. `sha256` is **mandatory** —
`get` refuses an entry without one, and verifies the download against
it (mismatch = hard error, bad download deleted). Tarballs that escape
the registry directory or contain absolute/`..` paths are rejected.
`publish` updates the index atomically (temp file + rename).

### Registry selection

`NIKO_REGISTRY` (index URL, `file://` URL, or local directory path)
wins; else `~/.niko/config.toml`:

```toml
[registry]
url = "https://example.com/niko/index.json"
```

No registry configured is a plain-English error naming both options.
**Network rule** (extends Alpha 16): only `niko2 get` and `niko2
publish` ever touch the network. Compile/run/check/wasm/native/debug/
lsp resolve packages from the cache only.

### CLI surface

- `niko2 get <name>` / `niko2 get <name>@<range>` — install the highest
  registry version satisfying the range (default `*`), then write the
  lockfile pin `{version, source: "registry:<spec>", range}` into the
  nearest enclosing `niko.lock` (or a new one in cwd).
- `niko2 get --update <name>` — re-resolve from the registry using the
  lockfile pin's range (falling back to the install record, else `*`);
  installs + re-pins only when a newer matching version exists
  (registry packages only; otherwise a clear error). Both flag
  positions work (`get --update <name>` and `get <name> --update` —
  the pre-existing argparse quirk needed an argv pre-scan for the
  former; `niko2 get --force <src>` was already broken the same way).
- `niko2 publish [--registry <dir|url>] [--force]` — publish the cwd
  package (tarball excludes `.git/`, `__pycache__/`, `*.pyc`).
  Overwriting an existing version needs `--force`; remote (http/https)
  registries are refused: "publishing needs auth, which is not
  supported yet".

### Disambiguation: registry spec vs dir/git

`is_registry_spec(arg)`: `<name>` or `<name>@<range>` where the name
matches the package-name regex (so it can contain no `/`, `.` or URL
scheme by construction) and a present range parses and contains no `/`
or scheme. Anything else falls through to Alpha 16 dir/git handling:

```
greet            registry lookup
greet@^1.0       registry lookup, range ^1.0
./greet          local directory (name part "./greet" invalid)
greet.git        git URL (name part "greet.git" invalid)
https://x/y.git  git URL
```

Consequence: a bare local directory named exactly like a package is
treated as a registry lookup — spell it `./dir` to force directory
handling. An unparseable range (`greet@junk`) also falls through
(clean "not a directory and not a git URL" error); an empty range
(`greet@`) reaches `split_registry_spec`, which errors explicitly.

### Version selection and the lockfile

- `get` always installs the **highest** version satisfying the
  requested range — it cannot pick a lower one. The pin is rewritten to
  exactly what was installed and the install is announced, so a version
  move is never silent. (Giving an explicitly narrower range than the
  pin, e.g. `get pkg@^1.0` after `get pkg`, moves the pin down to 1.x —
  the user asked for the 1.x line, and the message says so.)
- `niko2 lock` **never silently upgrades**: a previously pinned version
  still installed is kept, and the pin's `range` is preserved (lockfile
  entries without a range keep the Alpha 16 `{version, source}` shape).
- `--update` re-pins only when the registry has a newer version
  matching the pin's range; otherwise a friendly no-op.

### Dependencies

A manifest's top-level `[dependencies]` table maps names to ranges
(`lib = "^1.0"`; names and ranges validated at parse time). `get`
installs the full transitive closure from the **same registry**, one
level at a time, skipping any dependency that already has a satisfying
version in the cache (no network, no reinstall). Dependency cycles
terminate: recursion only happens when nothing cached satisfies the
range, and each recursion strictly grows the cache. Conflicting ranges
may install two versions of one package (npm's tradeoff).

Lockfile note: only directly-installed packages get pins
(`niko2 get` writes the pin; `niko2 lock` pins what the project
imports). Transitive dependencies are NOT pinned — their
reproducibility relies on the cache plus the declared ranges. Module
import cycles inside packages are still caught by `modules.py`.

### Import-time behavior: unchanged

No version-range solving at import time. `import "pkg:name/…"` still
resolves lockfile pin → newest cached → friendly error; the solver runs
only in `get`/`--update`, which guarantee a satisfying version is
cached.

## Files

- `niko2/semver.py` (new, ~330 lines): `parse_version`,
  `parse_range`, `satisfies`, `max_satisfying`, `SemverError`. Stdlib
  only, no intra-package imports.
- `niko2/registry.py` (new, ~700 lines): index protocol
  (`fetch_index`, `registry_versions`), registry selection
  (`resolve_registry`: `NIKO_REGISTRY` → `~/.niko/config.toml`),
  `install_from_registry` (download + sha256 verify + safe extract +
  dependency closure), `publish_package` (tarball build, atomic index
  update, remote refused), `update_package` (re-resolve + re-pin),
  `is_registry_spec`/`split_registry_spec`. Imports `packages.py`,
  never the reverse.
- `niko2/packages.py`: manifest gains `dependencies: dict`
  (`[dependencies]` validated via `semver`); lockfile `"packages"`
  entries gain an optional `range` (backwards-compatible); new
  `find_lock_dir` + `write_package_pin` helpers.
- `niko2/cli.py`: `get` routes registry specs to
  `install_from_registry` and writes the pin; `--update` flag
  (argv pre-scan for the `get --update <name>` position);
  `publish` command with `--registry`/`--force`.
- `tests/test_registry.py` (new): hermetic (local-dir registry,
  sandboxed cache, no network, real `~/.niko` untouched — asserted).
  Semver unit tests; disambiguation; publish (index/tarball layout,
  excludes, overwrite/`--force`/remote guards); get by name/range
  (max satisfying, exact, pin written); lock keeps pin; `--update`
  (upgrade, range-limited, no-op, error cases); sha256 mismatch hard
  error; registry selection (dir, `file://`, config file, env-wins,
  bad scheme, missing index); git-URL `get` unaffected;
  transitive deps (closure, skip-satisfied, cycle terminates,
  unsatisfiable dep errors).

## As built

Verified end-to-end against the working tree (implementation worker's
code), not just read:

- Published greetpkg 1.0.0/1.1.0/2.0.0 to a local-dir registry;
  `get greetpkg` → 2.0.0, `get greetpkg@^1.0` → 1.1.0,
  `get greetpkg@1.0.0` → 1.0.0; pins written as
  `{version, source: "registry:<spec>", range}`; `niko2 lock` keeps
  version + range; re-`get` is idempotent.
- `get --update` after publishing 2.1.0 upgrades 2.0.0 → 2.1.0 and
  re-pins (range kept); second run is a no-op; with pin range `^1.0`
  it upgrades 1.1.0 → 1.2.0 (not 2.1.0). Both `get --update <name>`
  and `get <name> --update` work.
- Corrupted tarball → `sha256 mismatch for "badpkg" 1.0.0 … (deleted
  the bad download)`, nothing installed.
- `NIKO_REGISTRY` as dir path and `file://` URL both work; config-file
  registry works when the env var is unset; env wins when both are
  set; `ftp://` → "bad registry"; empty/missing → "no registry
  configured"; dir without `index.json` → "not a Niko registry".
- Remote publish refused with the auth message via both `--registry
  https://…` and `NIKO_REGISTRY=https://…`; overwrite refused without
  `--force`.
- `get ./dironly` (dir named like a package) installs from disk while
  bare `get dironly` does a registry lookup; `get dironly@junk-range`
  falls through to the clean dir/git error; `get greetpkg@` errors
  "empty version range".
- Transitive chain app → lib ^1.0 → base >=1.0 installs app 1.0.0,
  lib 1.1.0 (max satisfying), base 1.0.0; a pre-installed satisfying
  lib 1.0.0 is left alone (no 1.1.0 appears); a↔b cycle terminates;
  `lib = "^9.0"` dep errors naming the dep and range.
- `file://` git-URL installs still work (`.git` stripped); tarball
  excludes (`.git/`, `__pycache__/`, `*.pyc`) verified by listing
  tarball members.
- Not covered hermetically: remote (http) index fetch/download (would
  need a network server in tests); exercised only via code reading.
- One docstring wrinkle found in testing: `is_registry_spec`'s
  docstring says `"greet@"` is NOT a registry spec, but `parse_range('')`
  succeeds so it returns True — harmless because `split_registry_spec`
  then raises the clear "empty version range" error; the test pins the
  CLI behavior, not the docstring sentence.

### Deliberate limits carried forward

No auth and no remote publish (clear errors, not silent gaps);
transitive dependencies are not pinned in the lockfile (cache +
declared ranges carry reproducibility); no solving at import time
(imports stay version-free, pins decide); `niko2 get --force <src>`
still broken by the pre-existing argparse quirk (documented in
KNOWN_LIMITATIONS).
