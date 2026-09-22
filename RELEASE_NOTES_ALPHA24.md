# Niko 2.0 — Alpha 24: Registry hardening (HTTP support + pinned transitive closure)

Two things that were half-true in Alpha 19 are now fully true: **registries
over HTTP(S) actually work** (with a short timeout and plain-English
errors), and **`niko.lock` now pins the whole dependency tree**, so a
committed lockfile reproduces the exact same packages on a fresh machine.

## What's new

**HTTP(S) registries work end to end.** Point `NIKO_REGISTRY` at an index
URL (`http://…/index.json`, or `https://`) and `niko2 get <name>` fetches
the index and the tarballs over HTTP, verifying every sha256 as before.
`file://` URLs never touch the network stack (plain local copy).

**Network failures speak plain English, and fail fast.** The fetch timeout
is now **10 seconds** (was 30) for both the index and every tarball, and
the common failure modes get their own messages instead of urllib's raw
text:

```
$ NIKO_REGISTRY=http://127.0.0.1:9/index.json niko2 get greetpkg
Niko error: could not fetch registry index from http://127.0.0.1:9/index.json:
connection refused -- is anything serving the registry at 127.0.0.1?
```

DNS failures, timeouts (`timed out after 10 seconds -- the registry may be
down or unreachable`), and HTTP statuses (`the server replied with HTTP 404
Not Found`) are all covered. A bad download can never leave a
half-installed package behind: the cache directory is only touched *after*
the tarball is downloaded **and** its sha256 verified **and** its manifest
checks pass (temp dirs are always cleaned up).

**`niko2 lock` pins the full transitive closure.** Transitive dependencies
now get `{version, source, range}` pins just like top-level imports —
`range` is the range the *parent package* asked for. Re-locking still never
silently upgrades: a pinned version that is still installed and still
satisfies the parent's range is kept; otherwise the newest *installed*
satisfying version is pinned. Two live requirements that admit no common
version are a hard error naming both parents, both ranges, and the fix
(`niko2 get <dep>@<range>` with a range both accept, then `niko2 lock`).
Dependency cycles terminate.

**Locked reinstall: `get` reproduces the pinned tree on a fresh machine.**
After every registry `get`, the CLI installs any lockfile-pinned
(registry-source) versions that aren't cached yet — exactly as pinned,
from the pin's own `source`. Strictly additive: nothing is ever downgraded,
replaced, or removed. When it installs anything it says so:

```
✓ installed locked dependencies: clib 1.1.0
```

**`get --update` re-resolves the subtree.** After rewriting the top-level
pin, the updated package's new `[dependencies]` ranges are resolved with
the same rule as `lock` and re-pinned — a narrowed dep range re-pins the
dep instead of leaving a stale pin behind. Pins outside the updated
package's subtree are untouched.

## Example session

```bash
# publish two packages to a local dir registry (as in Alpha 19) ...
$ niko2 publish --registry ./registry        # in greetlib/
$ niko2 publish --registry ./registry        # in greetapp/ (deps: greetlib ^1.0)

# ... serve it over plain HTTP (any static file host works) ...
$ python3 -m http.server 8471 --directory ./registry &
$ export NIKO_REGISTRY=http://127.0.0.1:8471/index.json

# ... and install over HTTP:
$ niko2 get greetapp
✓ installed greetapp 1.0.0 → /home/you/.niko/packages/greetapp-1.0.0

$ niko2 lock .
✓ wrote /home/you/proj/niko.lock
$ cat niko.lock
{
  "packages": {
    "greetapp": {
      "version": "1.0.0",
      "source": "registry:http://127.0.0.1:8471/index.json",
      "range": "*"
    },
    "greetlib": {
      "version": "1.1.0",
      "source": "registry:http://127.0.0.1:8471/index.json",
      "range": "^1.0"
    }
  }
}
```

A worked version of this lives in `examples/registry-http/` (`demo.sh`
does publish → serve → get → lock → run; `README.txt` explains the
static-hosting layout).

## Known limits

- **Nested `pkg:` imports resolve to the newest *cached* version, not the
  pin.** A `pkg:` import *inside* an installed package is resolved by
  walking up from the cache directory, where there is no `niko.lock` — so
  it sees the newest cached version. Direct imports from your project
  always honor the pin. (Pre-existing Alpha 16 behavior; see
  `niko2/KNOWN_LIMITATIONS.md`.)
- **No auth, no remote publish.** `niko2 publish` to an `http(s)://`
  registry is still refused (`publishing needs auth, which is not
  supported yet`).
- A bare local directory named exactly like a package is still treated as
  a registry lookup — spell it `./dir` to force directory handling.

## Tests

`tests/test_registry.py` gains 11 Alpha 24 sections (all hermetic: the
registry is published to a local dir, then served over HTTP on 127.0.0.1
with an ephemeral port in an in-process thread; sandboxed
`NIKO_PKG_CACHE`/`HOME`; no real network; real `~/.niko` asserted
untouched): HTTP get by name/range, sha256 mismatch over HTTP (no partial
install, no temp litter), refused-port fails fast + in-process error
taxonomy (HTTP status / DNS / refused / timeout), malformed index and
missing-`packages`-table over HTTP, HTTP 404, closure pinning with parent
ranges, locked reinstall on a fresh cache, pin-vs-newest-cached resolution
(including the nested-import limit, pinned as a test), `get --update`
subtree re-pinning, cycle termination in `get` *and* `lock`
(timeout-guarded), conflicting requirements error, and backfill
additivity + local-dir/`file://` regression. Full suite green
(26 Niko 1 cases, 16 Niko 2 cases + nikoir round trip, all
`tests/test_*.py`).

See `ALPHA24_DESIGN.md` for the design (HTTP behavior, error taxonomy,
lockfile shape, pin/conflict/update rules, the static-hosting recipe).
