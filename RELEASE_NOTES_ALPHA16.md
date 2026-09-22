# Niko 2.0 — Alpha 16 release notes (2026-09-22)

**The package manager arrives.** Niko 2 programs can now share reusable
libraries through packages. Publish a package as a folder (or a git repo)
with a `niko.toml` manifest; install it with one command; import it with
the `pkg:` prefix. Versions are pinned in `niko.lock` so builds stay
reproducible. There is no registry server — `niko2 get` installs from a
local directory or a git URL, and everything else (compile, run, check,
WASM/native, debug, LSP) resolves packages **offline** from the local
cache.

```niko
# niko.toml (the package being published)
[package]
name = "hello-pkg"
version = "1.0.0"
description = "A tiny example package."
```

```console
$ niko2 get examples/packages/hello-pkg
✓ installed hello-pkg 1.0.0 → /home/casper/.niko/packages/hello-pkg-1.0.0

$ niko2 run examples/packages/hello-app/main.niko
Hello, Casper, from a Niko package!
```

```niko
# the consumer program
import "pkg:hello-pkg/greet.niko" as greet
say greet.greet("Casper")
```

Try it yourself: `examples/packages/` holds a worked example (a published-style
package plus a consumer), with step-by-step instructions in
`examples/packages/README.txt`.

## How it works

- **`niko2 get <directory|git-url>`** is the only command that ever touches
  the network (a shallow `git clone --depth 1`; the `.git` metadata is
  stripped). Installing from a directory needs no network at all.
- Packages live in **`~/.niko/packages/<name>-<version>/`** (override with
  `NIKO_PKG_CACHE`). Each cached copy carries a `.niko-source.json`
  provenance record so `niko2 lock` can record where it came from.
- **No version in the import string.** `import "pkg:<name>/path/file.niko"`
  takes no version — versions live in the lockfile. Resolution: the pinned
  version from the nearest enclosing `niko.lock` wins; with no lockfile,
  the newest cached version is used; nothing cached gives the friendly
  `unknown package "foo" — run 'niko2 get <source>' to install it first`.
- **`niko2 lock` never silently upgrades.** Re-locking keeps a previously
  pinned version that is still installed; it pins the newest cached version
  only when there's no pin yet, and errors (naming `niko2 get`) if an
  imported package isn't installed at all.
- **Manifest rules:** the `[package]` table is canonical (`name`, `version`,
  `entry` defaulting to `main.niko`, optional `description`); Alpha 4-era
  flat keys still work (`[package]` wins if both appear); unknown fields
  are ignored for forward compatibility. Names must match
  `^[A-Za-z][A-Za-z0-9_-]*$`; versions are strict `X.Y.Z` (no leading
  zeros). `niko2 init` now writes the canonical form.
- **Offline guarantee:** `import "pkg:…"` never hits the network. A
  pinned-but-evicted package (lock says 9.9.9, only 1.0.0 cached) errors
  explicitly instead of resolving the wrong code.
- `import "pkg:…"` must name a `.niko` file, and `..` cannot escape the
  cached package directory. `import "stdlib/text.niko"` resolves exactly as
  before — packages don't collide with the bundled stdlib.

**Deliberate limits (for now):** no registry server, no `publish` command,
no version-range solving, no `niko` command alias (the repo already has a
Niko 1 `niko.py`; `niko2` keeps the two unambiguous). See
`ALPHA16_DESIGN.md` and `niko2/KNOWN_LIMITATIONS.md`.

**Housekeeping:** `niko2 get <src> --force` reinstalls over an already-cached
package (plain `niko2 get` is idempotent — second run is a no-op message).
Flags must come **after** the source (`niko2 get <src> --force`, not
`niko2 get --force <src>`) — a pre-existing argparse quirk shared with
other commands.

Full suite green: 26 Niko 1 cases, 15 Niko 2 cases + nikoir round trip, all
`tests/test_*.py`.
