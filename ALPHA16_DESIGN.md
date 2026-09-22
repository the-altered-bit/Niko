# Alpha 16 design: package manager

## Goal

Let Niko 2 programs reuse libraries written by other people: publish a
package as a folder or git repo carrying a `niko.toml` manifest, install it
with `niko2 get`, and import it with the `pkg:` prefix. Versions pin in
`niko.lock` so builds are reproducible. Deliberately **no registry server,
no `publish` command, no version-range solving** — the package manager is
a thin, offline-friendly layer over local directories and git URLs.

## Design

### Import syntax

```niko
import "pkg:<name>/path/to/file.niko" as alias
```

No version in the string — versions live in the lockfile. This keeps
program text stable while versions move underneath it (pinned by
`niko2 lock`, otherwise newest cached).

### One network touchpoint

`niko2 get <source>` (`niko2/packages.py::install_package`) is the **only**
place in the toolchain that touches the network. Sources:

- a local directory — must contain `niko.toml` (no network needed), or
- a git URL — `https://`, `http://`, `git@`, `ssh://`, `file://`
  (anything else that ends in `.git` is also treated as git).
  Shallow `git clone --depth 1`; the `.git` metadata is stripped from the
  cached copy.

Everything else — `run`, `check`, `wasm`, `native`, `debug`, `lsp`,
`import` resolution — reads the cache only. A pinned-but-evicted package
(lock says 9.9.9, only 1.0.0 cached) errors explicitly naming the expected
version, never silently resolving the wrong code.

### Cache layout

`~/.niko/packages/<name>-<version>/`, overridable with `NIKO_PKG_CACHE`.
Each cached package carries `.niko-source.json` —
`{"source": "<url-or-path>", "kind": "git"|"local"}` — provenance for
`niko2 lock` (so `lock` can record where a version came from without
re-asking the user).

### Manifest (`niko.toml`)

Canonical form is the `[package]` table:

```toml
[package]
name = "acme-utils"
version = "1.2.0"
entry = "main.niko"     # optional, defaults to main.niko
description = "..."     # optional free text
```

Alpha 4-era flat top-level keys (`name = …` with no `[package]` table)
still work; `[package]` wins when both are present. **Unknown fields are
ignored** (forward compatibility — old toolchains keep working when new
optional fields appear). Enforced rules: name matches
`^[A-Za-z][A-Za-z0-9_-]*$`; version is strict `X.Y.Z` (no leading zeros);
`entry` must name a `.niko` file and must exist in the package directory.
Every failure raises `PackageError` naming the file and the problem.
`niko2 init` now writes the canonical `[package]` form.

### Version selection

- `import "pkg:foo/…"` resolves: **lockfile pin wins** (from the nearest
  enclosing `niko.lock`, walked up from the importing file's directory);
  else **newest cached** (semver sort); nothing cached → friendly
  `unknown package "foo" — run 'niko2 get <source>' to install it first`.
- `niko2 lock` **never silently upgrades**: a previously pinned version
  that is still installed is kept; otherwise the newest cached version is
  pinned; an imported package that isn't installed at all is a hard error
  naming `niko2 get`.
- Idempotent installs: already cached → no-op message
  (`… is already installed (…) -- use --force to reinstall`); `--force`
  reinstalls. The manifest's name/version are authoritative — the source
  directory's own name is ignored.

### Resolution order

`modules.resolve_import` checks the `pkg:` prefix **before** the old
Alpha 14 search order, so `pkg:` never collides with a relative path, a
`NIKO_PATH` entry, or the bundled stdlib (`import "stdlib/text.niko"`
resolves exactly as before). `..` escapes from inside a cached package
directory are rejected.

## Files

- `niko2/packages.py` (~440 lines): manifest parsing/validation
  (`parse_manifest`), `install_package` (the `niko2 get` implementation),
  `resolve_package*`/`resolve_pkg_spec` (offline `pkg:` resolution),
  lockfile helpers (`lock_packages_for_project`,
  `locked_package_versions`, `collect_package_imports`). Stdlib-only
  imports — importable from `modules.py` and `project.py` without cycles.
- `niko2/modules.py`: `_resolve_pkg_import` hook — first thing in
  `resolve_import`; lockfile pins read from the importing file's
  directory; `PackageError` translated to `ImportErrorNiko` with line/col.
- `niko2/project.py`: `DEFAULT_MANIFEST` now canonical `[package]` form;
  `read_manifest` merges `[package]` over flat keys (Alpha 4 compat);
  `_package_imports_for_file` feeds both `niko2 deps` and `niko2 lock`;
  `write_lock_file`/`read_lock_file` add the `"packages"` table —
  `"packages": {"<name>": {"version": "X.Y.Z", "source": "<url-or-path>"}}`
  — leaving the existing `{"version", "dependencies"}` shape untouched.
- `niko2/cli.py`: `get` subcommand (usage printed when no source given);
  `lock` wraps `write_lock_file` in try/except so an uninstalled package
  prints a clean `Niko error:` instead of a traceback.

## As built

Every decision above was verified against the working tree (worker 1's
implementation) by running it end-to-end, not just reading the code:

- `niko2 get` from a local dir and from a `file://` git URL both install
  into `<cache>/<name>-<version>/` with `.git` stripped and a correct
  `.niko-source.json` (`kind: local` / `kind: git`).
- Lock pin beats newer cached version (installed greet 1.0.0, locked it,
  installed 1.1.0 — the program still resolved 1.0.0); deleting the lock
  switches resolution to the newest cached (1.1.0); re-locking with a
  valid pin keeps it (no silent upgrade).
- Pinned-but-evicted (lock says 9.9.9) errors:
  `package "greet" is locked to version 9.9.9 but it is not installed —
  run 'niko2 get <source>' to install it`; unknown package errors:
  `unknown package "nosuchpkg" — run 'niko2 get <source>' to install it
  first`.
- `niko2 deps` shows `pkg:` imports per file; the lockfile carries the
  `"packages"` table alongside the untouched `"dependencies"` table.
- Manifest validation errors are all plain-English with the file named
  (bad name, bad version, missing `niko.toml`, missing entry file);
  flat manifests install fine; unknown `[package]` fields and extra
  tables are ignored.
- `..` escapes rejected (`import "pkg:greet/../greet/greet.niko"` →
  `escapes the package directory`); non-`.niko` paths rejected;
  `import "stdlib/text.niko"` behavior unchanged.
- Pre-existing argparse quirk carried over from the other subcommands:
  flags must come **after** positionals — `niko2 get <src> --force`
  works; `niko2 get --force <src>` is rejected as unrecognized
  arguments. Docs show the working order.

### `niko` alias — deliberately not added

The sprint considered adding a `niko = "niko2.cli:main"` console script
so `niko get` would literally work. Decided **against**: the repo root
already has `niko.py` (Niko 1), and a bare `niko` command would be
genuinely ambiguous — a user typing `niko run foo.niko` might reasonably
expect Niko 1 semantics. The command stays `niko2 get`; worker 1's
`packages.py` messages that said `niko get` were normalized to
`niko2 get` to match. Revisiting this means owning the Niko 1/2 command
ambiguity first.
