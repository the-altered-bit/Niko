#!/bin/bash
# Alpha 24 worked example: publish -> serve over HTTP -> get over HTTP.
# Everything lives in throwaway dirs (mktemp); the real ~/.niko is never
# touched. Run from the repo root:  bash examples/registry-http/demo.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
WORK="$(mktemp -d)"
SRV_PID=""
cleanup() {
    [ -n "$SRV_PID" ] && kill "$SRV_PID" 2>/dev/null || true
    rm -rf "$WORK"
}
trap cleanup EXIT

export NIKO_PKG_CACHE="$WORK/cache"
export PYTHONPATH="$ROOT"

# 1. two tiny packages: greetapp 1.0.0 depends on greetlib ^1.0
mkdir -p "$WORK/greetlib" "$WORK/greetapp" "$WORK/registry" "$WORK/proj"
cat > "$WORK/greetlib/niko.toml" <<'EOF'
[package]
name = "greetlib"
version = "1.0.0"
EOF
cat > "$WORK/greetlib/main.niko" <<'EOF'
to hello with who:
    give back "hi " + who
EOF
cat > "$WORK/greetapp/niko.toml" <<'EOF'
[package]
name = "greetapp"
version = "1.0.0"

[dependencies]
greetlib = "^1.0"
EOF
cat > "$WORK/greetapp/main.niko" <<'EOF'
import "pkg:greetlib/main.niko" as g
say g.hello("Casper")
EOF

# 2. publish both to a local directory registry
(cd "$WORK/greetlib" && python3 -m niko2 publish --registry "$WORK/registry")
(cd "$WORK/greetapp" && python3 -m niko2 publish --registry "$WORK/registry")

# 3. serve the registry dir over HTTP on 127.0.0.1 (ephemeral port)
PORT="$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
python3 -m http.server "$PORT" --directory "$WORK/registry" >/dev/null 2>&1 &
SRV_PID=$!
sleep 1
export NIKO_REGISTRY="http://127.0.0.1:$PORT/index.json"

# 4-6. fresh project: get over HTTP, lock the closure, run
cd "$WORK/proj"
python3 -m niko2 get greetapp
cat > app.niko <<'EOF'
import "pkg:greetapp/main.niko" as a
EOF
python3 -m niko2 lock .
python3 -m niko2 run app.niko
echo '--- niko.lock ---'
cat niko.lock
