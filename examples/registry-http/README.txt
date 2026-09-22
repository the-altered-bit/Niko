Niko 2 — Alpha 24 worked example: a registry over HTTP
=======================================================

demo.sh does the whole loop with throwaway directories (nothing is
installed into your real ~/.niko — it sets NIKO_PKG_CACHE to a temp dir):

  1. writes two tiny packages (greetapp 1.0.0 depends on greetlib ^1.0)
  2. publishes both to a local directory registry
     (niko2 publish --registry <dir>)
  3. serves that directory over HTTP on 127.0.0.1
     (python3 -m http.server --directory <dir>)
  4. from a fresh project: niko2 get greetapp  (over HTTP)
  5. niko2 lock .                               (pins the full closure)
  6. runs a program importing pkg:greetapp/main.niko

Run it from the repo root:

    bash examples/registry-http/demo.sh

Expected output (paths/versions will match what the script writes):

    ✓ published greetlib 1.0.0 to .../registry
    ✓ published greetapp 1.0.0 to .../registry
    ✓ installed greetapp 1.0.0 → .../cache/greetapp-1.0.0
    ✓ wrote .../proj/niko.lock
    hi Casper
    { "packages": { "greetapp": {...}, "greetlib": {...} } }

The lockfile pins BOTH packages: greetapp (the direct import) and
greetlib (the transitive dep, with range "^1.0" — the range greetapp
asked for).

Hosting your own registry
--------------------------
A registry is just static files: index.json + tarballs/, exactly what
`niko2 publish --registry <dir>` produces. Serve <dir> with any static
file host (GitHub Pages, S3, nginx, python3 -m http.server) and point
NIKO_REGISTRY at https://your-host/<path>/index.json. sha256 is
mandatory in the index and verified on every download; publishing to a
remote registry is refused (no auth story yet — publish locally, then
sync the directory to your host).
