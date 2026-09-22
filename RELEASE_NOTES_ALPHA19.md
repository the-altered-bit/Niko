# Niko 2.0 — Alpha 19 release notes (2026-09-22)

**The package manager grows a registry.** Alpha 16 let you install
packages from a folder or a git URL; now you can publish versioned
packages to a registry and install them by name with version ranges.
`niko2 get greet` installs the newest `greet`; `niko2 get greet@^1.0`
installs the newest 1.x; `niko2 get --update greet` upgrades a pinned
package when a newer matching version appears. A registry is just a
directory with an `index.json` plus tarballs (a remote index over HTTP
works for installs too), and every tarball is sha256-verified on
download. Compiling, running, and everything else still resolve
packages **offline** from the local cache — only `get` and `publish`
ever touch the network.

```console
$ niko2 publish --registry /srv/niko-registry
✓ published greet 1.1.0 to /srv/niko-registry

$ export NIKO_REGISTRY=/srv/niko-registry   # or [registry] url = ... in ~/.niko/config.toml
$ niko2 get greet@^1.0
✓ installed greet 1.1.0 → /home/casper/.niko/packages/greet-1.1.0

$ niko2 get --update greet
greet is already at the newest matching version (1.1.0)
```

```niko
# the consumer program (unchanged — imports take no version)
import "pkg:greet/main.niko" as greet
say greet.hello("Casper")
```

Try it yourself with a local registry: publish any package folder with
`niko2 publish --registry <dir>`, point `NIKO_REGISTRY` at `<dir>`, and
`niko2 get <name>` — no network needed.

## How it works

- **Version ranges** (`niko2/semver.py`): `*`, `1.2.3`, `1.2` (=`1.2.x`),
  npm-style `^1.2.3` (leftmost non-zero component pinned, so `^0.2.3`
  stays in 0.2.x) and `~1.2.3` (same minor), comparators
  `>=`/`>`/`<=`/`<`, `=`/`==`, and comma AND (`>=1.0, <2.0`). The solver
  picks the highest version satisfying the range; bad ranges are
  plain-English errors.
- **`niko2 get <name>[@<range>]`** installs the newest matching version
  and writes the pin `{version, source: "registry:<spec>", range}` into
  `niko.lock`. `niko2 get <name>` alone means the newest version, range
  `*`.
- **`niko2 get --update <name>`** re-resolves using the pin's range and
  upgrades the install + pin only when a newer matching version exists;
  otherwise a friendly "already at the newest matching version".
- **`niko2 publish [--registry <dir>] [--force]`** builds a tarball
  (`.git/`, `__pycache__/`, `*.pyc` excluded), records its sha256, and
  updates the registry index atomically. Overwriting a published version
  needs `--force`.
- **Integrity:** `sha256` is mandatory in the index; a mismatch is a
  hard error and the bad download is deleted. Tarballs can't escape the
  registry directory or contain absolute/`..` paths.
- **Disambiguation:** a bare word is a registry lookup, so a local
  directory named exactly like a package needs `./` (`niko2 get
  ./mydir`); anything with a `/`, `.`, or URL scheme falls through to
  the old folder/git-URL handling, which is unchanged.
- **Dependencies:** packages declare `[dependencies]` (`lib = "^1.0"`)
  in `niko.toml`; `get` installs the full transitive closure from the
  same registry, skipping anything already cached that satisfies the
  range. Only directly-installed packages get lockfile pins.
- **Selection:** `NIKO_REGISTRY` (index URL, `file://` URL, or local
  dir) wins; otherwise `~/.niko/config.toml` `[registry] url`.

**Deliberate limits (for now):** no auth and no publishing to remote
registries (clear errors, not silent gaps); transitive dependencies are
not pinned in the lockfile; no version-range solving at import time
(imports stay version-free — the lockfile pin decides); `niko2 get
--force <src>` is still rejected by the pre-existing argparse quirk
(flags go after positionals, except `get --update <name>` which is
special-cased). See `ALPHA19_DESIGN.md` and `niko2/KNOWN_LIMITATIONS.md`.

Full suite green: 26 Niko 1 cases, 15 Niko 2 cases + nikoir round trip,
all `tests/test_*.py` (new: `tests/test_registry.py`, hermetic — local
registry fixture, no network, real `~/.niko` untouched).
