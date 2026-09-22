# Alpha 24 design: registry hardening (HTTP support + pinned transitive closure)

## Problem

Alpha 19 shipped the registry protocol and version-range solving, but two
things were half-finished:

1. **Remote registries were declared but barely usable.** `_FETCH_TIMEOUT`
   was 30 seconds (a dead host hangs scripts and CI), and every network
   failure surfaced urllib's raw exception text. The `file://` branch was
   fine; `http(s)://` had no hermetic test coverage at all.
2. **Transitive dependencies were not pinned.** `niko.lock` held only
   directly-`get` packages; a transitive dep's exact version came from
   "newest cached satisfying the range", so a committed lockfile did not
   reproduce the same tree on a fresh machine — the installer could pick a
   *newer* satisfying version than the one the lockfile author had.

## Design

### HTTP behavior

- `_FETCH_TIMEOUT`: **30 → 10 seconds**, applied to *both* network paths:
  `_read_remote_index` (the index fetch) and `_download`'s `http(s)` branch
  (every tarball). Rationale: 10s is long enough for a healthy registry
  (index + tarballs are small) and short enough that a dead host fails fast
  instead of hanging scripts, tests, and CI. `urlopen`'s `timeout` covers
  both connect and read. The `file://` branch is a plain `shutil.copyfile`
  — it never goes through the network stack, so it never needs the timeout.
- **Error taxonomy** via `_network_error(what, url, e)` → `PackageError`
  (plain English, no urllib jargon):
  - `HTTPError` → `could not fetch <what> from <url>: the server replied
    with HTTP <code> <reason>` (e.g. `HTTP 404 Not Found`).
  - `URLError` wrapping `socket.gaierror` → `could not resolve "<host>"
    (DNS failure) -- check the registry address`.
  - `URLError` wrapping `ConnectionRefusedError` → `connection refused --
    is anything serving the registry at <host>?`.
  - `URLError` wrapping `socket.timeout` (alias of `TimeoutError` since
    3.10) → `timed out after 10 seconds -- the registry may be down or
    unreachable`.
  - Anything else → the old `could not fetch <what> from <url>: <e>`
    fallback (never worse than before).
- **No-partial-install ordering** (documented at the call site):
  `_download_and_verify` downloads to a temp dir and verifies sha256
  *before* returning; `install_from_registry` only touches the cache `dest`
  directory after download + sha256 + manifest/entry checks all pass. A
  failed download, a hash mismatch, or a bad tarball can never leave a
  half-installed package behind — the temp dir is always removed, and
  `dest` is replaced only at the single success point.
- **Redirect policy:** urllib follows GET redirects by default; a redirect
  that lands on something that isn't a JSON index still fails in
  `_read_remote_index` with the clear `not valid JSON` message. No special
  redirect handling was added — redirects to a valid index just work.
- **`publish` to a remote registry still refuses** (`publishing needs
  auth, which is not supported yet` — tell the user to use a local
  directory registry). Auth is future work, not this alpha.

### Lockfile closure shape

`niko2 lock`'s `"packages"` table now holds the full transitive closure.
Each entry is `{version, source, range?}`; `range` on a *transitive* entry
is the range the **parent package** requested (first parent wins when
several parents agree on the pinned version). Example for a project
importing `pkg:capp/main.niko` where `capp 1.0.0` declares
`[dependencies] clib = "^1.0"`:

```json
{
  "packages": {
    "capp": {
      "version": "1.0.0",
      "source": "registry:http://127.0.0.1:8471/index.json",
      "range": "*"
    },
    "clib": {
      "version": "1.1.0",
      "source": "registry:http://127.0.0.1:8471/index.json",
      "range": "^1.0"
    }
  }
}
```

The shape is backwards-compatible with Alpha 16/19 lockfiles (`{version,
source}` entries without `range` are still accepted). Extra entries are
harmless to `locked_package_versions`, which reads only `{name: version}`
pairs.

### Pin rule (`packages._transitive_closure_pins`)

Resolution mirrors the installer's preference for cached versions and the
top-level "re-locking never silently upgrades" rule. Each dependency is
expanded at most once (cycle-safe: a↔b terminates):

1. **Already pinned** (in the current walk, in the seed top-level pins, or
   adopted from the *previous* lockfile): keep the pin when the pinned
   version is installed **and** satisfies the requesting parent's range.
2. Otherwise pin the **newest installed version** satisfying the range.
3. No installed version satisfies the range → hard error naming
   `niko2 get <dep>@<range>`.
4. A stale *prior* pin that no longer satisfies is re-resolved, never an
   error.

### Conflict rule

Two **live** requirements (both in the current graph) that admit no common
pinned version are a hard error naming both parents, both ranges, the
pinned version, and the fix:

```
conflicting requirements for package "confg": "confh" needs "^2.0", but it
is already pinned to 1.0.0 (required as "^1.0" by "conff") -- the lockfile
holds one version per package. To fix: run 'niko2 get confg@<range>' with a
range both requirements accept, then 'niko2 lock' again.
```

(Note: the *installer* may still cache two versions of one package to
satisfy conflicting ranges at install time — the npm tradeoff, unchanged.
The lockfile holds one version per package; the conflict surfaces at
`lock` time so it can't be committed silently.)

### Update semantics (`get --update`)

`update_package` rewrites the top-level pin as before, then calls
`packages.pin_closure_under(lock_dir, name)`: the updated package's *new*
`[dependencies]` ranges are resolved with the same rule as `lock`, and the
subtree's pins are rewritten via `write_package_pin`. Only the closure
beneath the updated package is touched; pins belonging to other top-level
packages are left alone, and stale entries (a dep the new version no longer
needs) are left in place — the next `niko2 lock` re-derives the whole
table from scratch and drops them.

### Locked reinstall (`registry.ensure_locked_closure_installed`)

After every registry `get`, the CLI walks the lockfile's `"packages"`
table and installs each pinned version that isn't cached yet — **exactly**
(the version string itself is the range), from the registry recorded in
the pin's own `source`. Strictly additive: never downgrades, replaces, or
removes; already-cached versions are skipped; non-registry pins (local
directory / git) are skipped (their source is authoritative for the
version, so "missing exact version" can't happen). Prints
`✓ installed locked dependencies: b 1.1.0` when it installs anything.
This is what makes `git clone` + `niko2 get <top>` reproduce the author's
exact tree on a fresh machine.

### Static-hosting recipe (future public registry)

A Niko registry is just static files — no server code needed. To host one:

```
my-registry/                  <- serve this dir over HTTPS
  index.json                  <- {"packages": {name: {version: {url, sha256, description, dependencies}}}}
  tarballs/
    foo-1.0.0.tar.gz
    foo-1.1.0.tar.gz
```

`niko2 publish --registry <dir>` produces exactly this layout (tarball
`url`s are relative, so the same directory works over `file://` and over
HTTP). Serve `<dir>` with any static file host (GitHub Pages, S3, nginx,
`python3 -m http.server`), then:

```
export NIKO_REGISTRY=https://example.com/niko/index.json
niko2 get foo
```

sha256 is mandatory in the index and verified on every download.

## As built

Verified end-to-end against the working tree, not just read:

- Published `httplib` 1.0.0/1.1.0/2.0.0 to a local dir, served it over HTTP
  on 127.0.0.1 (in-process `http.server`, ephemeral port): `get httplib`
  → 2.0.0, `get httplib@^1.0` → 1.1.0, pin `source` =
  `registry:http://127.0.0.1:<port>/index.json`; `_tarball_url` confirmed
  to produce `http://` URLs for remote registries.
- Corrupted tarball over HTTP → `sha256 mismatch for "badhttp" 1.0.0 …
  (deleted the bad download)`; nothing in the cache, no lockfile written,
  no `niko-reg-*` temp dir left behind (snapshotted `/tmp` before/after).
- Refused port → `connection refused -- is anything serving the registry
  at 127.0.0.1?` in well under a second (asserted < 5s so the test can't
  silently wait out the 10s timeout). In-process taxonomy checks pin the
  HTTP 404, DNS-failure, refused, and timeout messages, and assert
  `_FETCH_TIMEOUT == 10`.
- Non-JSON index over HTTP → `not valid JSON`; JSON without a `packages`
  table → `expected a JSON object with a "packages" table`; missing index
  path → message contains `HTTP 404`.
- `get capp` (deps `clib ^1.0`) + `lock` → `niko.lock` holds both `capp`
  `{1.0.0, source, range: "*"}` and `clib` `{1.1.0, source, range: "^1.0"}`
  (parent's range, newest installed satisfying).
- Published `clib 1.2.0` *after* the lock; fresh cache + `get capp` →
  `✓ installed locked dependencies: clib 1.1.0`, `clib-1.1.0` in the cache,
  and re-`lock` keeps 1.1.0 (no silent upgrade). Direct
  `import "pkg:clib/main.niko"` in the project resolves 1.1.0 (the pin);
  the nested import inside cached `capp` resolves 1.2.0 (newest cached) —
  the known limit, pinned as a test.
- `get --update upd` after publishing `upd 2.0.0` (deps `updep ^2.0`,
  narrowed from `^1.0`) → `updep` re-pinned to 2.0.0 with range `^2.0`.
- `cycA24 ↔ cycB24` terminates in both `get` and `lock`
  (timeout-guarded subprocess calls, 60s).
- `conff` (needs `confg ^1.0`) + `confh` (needs `confg ^2.0`) → `lock`
  fails with the conflict error naming both parents/ranges and the
  `niko2 get confg@<range>` fix.
- `ensure_locked_closure_installed` is a no-op when everything is cached;
  non-registry pins are skipped without fetching. With the HTTP server
  shut down, local-dir and `file://` registry `get`s still work.
- Test-hygiene lesson captured in the test file: `find_lock_dir` walks UP
  from the project dir, so the fixtures assert no stray `/tmp/niko.lock`
  or `/niko.lock` exists before running.

### Deliberate limits carried forward

- **Nested `pkg:` imports resolve to newest-cached, not the pin**
  (pre-existing Alpha 16 behavior, now tested + documented): a `pkg:`
  import *inside* a cached package walks up from the cache dir, where
  there is no `niko.lock`. Direct project imports always honor the pin.
- No auth, no remote publish (clear errors, not silent gaps).
- No version-range solving at import time (imports stay version-free; the
  lockfile pin decides, else newest cached).
- `niko2 get --force <src>` still broken by the pre-existing argparse
  quirk (documented in KNOWN_LIMITATIONS); `get <name> --update` works via
  the argv pre-scan.
