#!/usr/bin/env node
/* Alpha 29 playground end-to-end test.
 *
 * Drives REAL headless Chromium via CDP (WebSocket): loads the page, waits
 * for window.__nikoReady (Pyodide + compiler actually downloaded), runs the
 * fibonacci example in-page and asserts `6765` lands in the output pane,
 * then checks a broken program surfaces a compile error in the error pane.
 *
 *   node e2e.mjs            # run from examples/playground/
 *   NIKO_CHROME=/path/to/chrome node e2e.mjs   # override the Chrome binary
 *
 * Needs Node 18+ and a Chrome/Chromium binary. No npm dependencies: the
 * CDP client speaks WebSocket over a raw socket (Node < 21 has no WebSocket
 * global).
 *
 * Exit codes: 0 = pass; 2 = Pyodide CDN unreachable (skip, NOT a pass);
 * anything else = real failure with a message on stderr.
 *
 * Environment notes (verified on the build machine):
 * - /opt/meta-chromium/chrome is Chromium 152; --dump-dom is broken (empty
 *   output) but --remote-debugging-port works, so CDP is used for everything.
 * - Chromium 152's Local Network Access checks block top-level navigation
 *   from about:blank/file:// to http://127.0.0.1
 *   (net::ERR_BLOCKED_BY_LOCAL_NETWORK_ACCESS_CHECKS). The test therefore
 *   tries the served http:// URL first and falls back to the file:// page
 *   (a supported configuration — the page must work from file://) when the
 *   browser blocks local-network navigation. The fallback is logged loudly.
 * - The sandbox reaches the internet only through an egress proxy that
 *   requires Proxy-Authorization and MITMs TLS. When https_proxy/HTTPS_PROXY
 *   is set, the test runs a tiny in-process forward proxy that adds the
 *   Proxy-Authorization header upstream (Chrome cannot do proxy auth
 *   non-interactively) and launches Chrome with --proxy-server pointing at
 *   it, plus --ignore-certificate-errors (the MITM CA is not in Chrome's
 *   trust store; the flag is scoped to this test run only). Without a proxy
 *   env var, Chrome launches with the plain flags and no extra switches.
 */
"use strict";

import { spawn } from "node:child_process";
import http from "node:http";
import net from "node:net";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import crypto from "node:crypto";

const CHROME = process.env.NIKO_CHROME || "/opt/meta-chromium/chrome";
const PYODIDE_JS = "https://cdn.jsdelivr.net/pyodide/v0.29.1/full/pyodide.js";
const HERE = path.dirname(fileURLToPath(import.meta.url));

const fail = (msg) => {
  console.error("E2E FAIL:", msg);
  process.exit(1);
};
const skip = (msg) => {
  console.error("E2E SKIP:", msg);
  process.exit(2);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ---------------- tiny forward proxy ----------------
 * Adds Proxy-Authorization for an upstream proxy that needs it (Chrome
 * cannot answer a proxy auth challenge non-interactively). Loopback targets
 * are connected directly. Only used when https_proxy/HTTPS_PROXY is set.
 */
function startForwardProxy(upstream) {
  const auth =
    "Basic " +
    Buffer.from(
      `${decodeURIComponent(upstream.username)}:${decodeURIComponent(upstream.password)}`
    ).toString("base64");
  return new Promise((resolve) => {
    const server = net.createServer((client) => {
      let buf = Buffer.alloc(0);
      const onData = (chunk) => {
        buf = Buffer.concat([buf, chunk]);
        const eoh = buf.indexOf("\r\n\r\n");
        if (eoh === -1) return;
        client.removeListener("data", onData);
        const head = buf.slice(0, eoh).toString("latin1");
        const rest = buf.slice(eoh + 4);
        const [method, target] = head.split("\r\n")[0].split(" ");
        let host, port;
        if (method === "CONNECT") {
          [host, port] = target.split(":");
        } else {
          try {
            const u = new URL(target);
            host = u.hostname;
            port = u.port || "80";
          } catch {
            client.destroy();
            return;
          }
        }
        const direct = host === "127.0.0.1" || host === "localhost" || host === "::1";
        const dial = (done) => {
          const up = net.connect(
            direct ? +port : +upstream.port,
            direct ? host : upstream.hostname,
            () => done(up)
          );
          up.on("error", () => client.destroy());
          client.on("error", () => up.destroy());
        };
        if (method === "CONNECT" && !direct) {
          dial((up) => {
            up.write(
              `CONNECT ${target} HTTP/1.1\r\nHost: ${target}\r\n` +
                `Proxy-Authorization: ${auth}\r\nConnection: keep-alive\r\n\r\n`
            );
            let ub = Buffer.alloc(0);
            const wait200 = (c) => {
              ub = Buffer.concat([ub, c]);
              const e = ub.indexOf("\r\n\r\n");
              if (e === -1) return;
              up.removeListener("data", wait200);
              const st = ub.slice(0, e).toString("latin1").split("\r\n")[0];
              if (!/ 200\b/.test(st)) {
                client.write("HTTP/1.1 502 Bad Gateway\r\n\r\n");
                client.destroy();
                up.destroy();
                return;
              }
              client.write("HTTP/1.1 200 Connection Established\r\n\r\n");
              const leftover = ub.slice(e + 4);
              if (leftover.length) client.write(leftover);
              if (rest.length) up.write(rest);
              client.pipe(up);
              up.pipe(client);
            };
            up.on("data", wait200);
          });
        } else if (method === "CONNECT") {
          dial((up) => {
            client.write("HTTP/1.1 200 Connection Established\r\n\r\n");
            if (rest.length) up.write(rest);
            client.pipe(up);
            up.pipe(client);
          });
        } else {
          dial((up) => {
            up.write(direct ? head + "\r\n\r\n" : head + `\r\nProxy-Authorization: ${auth}\r\n\r\n`);
            if (rest.length) up.write(rest);
            client.pipe(up);
            up.pipe(client);
          });
        }
      };
      client.on("data", onData);
      client.on("error", () => {});
    });
    server.listen(0, "127.0.0.1", () => resolve(server));
  });
}

/* ---------------- static file server ---------------- */
const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".md": "text/plain; charset=utf-8",
  ".niko": "text/plain; charset=utf-8",
};

function startServer() {
  return new Promise((resolve, reject) => {
    const srv = http.createServer((req, res) => {
      try {
        let p = decodeURIComponent(req.url.split("?")[0]);
        if (p === "/") p = "/index.html";
        const file = path.join(HERE, path.normalize(p));
        if (!file.startsWith(HERE + path.sep)) {
          res.writeHead(403);
          res.end("forbidden");
          return;
        }
        fs.readFile(file, (err, data) => {
          if (err) {
            res.writeHead(404);
            res.end("not found");
            return;
          }
          res.writeHead(200, {
            "Content-Type": MIME[path.extname(file)] || "application/octet-stream",
          });
          res.end(data);
        });
      } catch {
        res.writeHead(500);
        res.end("error");
      }
    });
    srv.listen(0, "127.0.0.1", () => resolve(srv));
    srv.on("error", reject);
  });
}

function freePort() {
  return new Promise((resolve, reject) => {
    const s = net.createServer();
    s.listen(0, "127.0.0.1", () => {
      const p = s.address().port;
      s.close(() => resolve(p));
    });
    s.on("error", reject);
  });
}

/* ---------------- minimal WebSocket client (no deps) ----------------
 * Node < 21 has no global WebSocket. CDP only needs text frames on ws://,
 * so this implements just that: HTTP upgrade handshake, masked client
 * frames, ping/pong, close. Same handler style as the browser WebSocket
 * (onopen/onmessage({data})/onerror/onclose) so CDP below is unchanged.
 */
class WS {
  constructor(url) {
    this.u = new URL(url);
    this.onopen = null;
    this.onmessage = null;
    this.onerror = null;
    this.onclose = null;
    this.buf = Buffer.alloc(0);
    this.connected = false;
  }
  connect() {
    return new Promise((resolve, reject) => {
      const key = crypto.randomBytes(16).toString("base64");
      const port = this.u.port ? +this.u.port : 80;
      const target = `${this.u.pathname}${this.u.search}`;
      const sock = net.createConnection(
        { host: this.u.hostname, port },
        () => {
          sock.write(
            `GET ${target} HTTP/1.1\r\n` +
              `Host: ${this.u.hostname}:${port}\r\n` +
              `Upgrade: websocket\r\n` +
              `Connection: Upgrade\r\n` +
              `Sec-WebSocket-Key: ${key}\r\n` +
              `Sec-WebSocket-Version: 13\r\n\r\n`
          );
        }
      );
      this.sock = sock;
      let settled = false;
      const done = (fn, v) => {
        if (!settled) {
          settled = true;
          fn(v);
        }
      };
      sock.on("error", (e) => {
        if (this.onerror) this.onerror(e);
        done(reject, e);
      });
      sock.on("close", () => {
        if (this.onclose) this.onclose();
      });
      sock.on("data", (d) => {
        this.buf = Buffer.concat([this.buf, d]);
        if (!this.connected) {
          const i = this.buf.indexOf("\r\n\r\n");
          if (i < 0) return;
          const head = this.buf.slice(0, i).toString("latin1");
          this.buf = this.buf.slice(i + 4);
          if (!/^HTTP\/1\.1 101 /m.test(head)) {
            done(reject, new Error("WS upgrade failed: " + head.split("\r\n")[0]));
            return;
          }
          this.connected = true;
          if (this.onopen) this.onopen();
          done(resolve);
        }
        while (this.buf.length >= 2) {
          const b0 = this.buf[0];
          const b1 = this.buf[1];
          const opcode = b0 & 0x0f;
          let len = b1 & 0x7f;
          let off = 2;
          if (len === 126) {
            if (this.buf.length < 4) return;
            len = this.buf.readUInt16BE(2);
            off = 4;
          } else if (len === 127) {
            if (this.buf.length < 10) return;
            len = Number(this.buf.readBigUInt64BE(2));
            off = 10;
          }
          const masked = (b1 & 0x80) !== 0;
          let mask = null;
          if (masked) {
            if (this.buf.length < off + 4) return;
            mask = this.buf.slice(off, off + 4);
            off += 4;
          }
          if (this.buf.length < off + len) return;
          const payload = this.buf.slice(off, off + len);
          if (mask) for (let i = 0; i < payload.length; i++) payload[i] ^= mask[i % 4];
          this.buf = this.buf.slice(off + len);
          if (opcode === 0x8) {
            sock.end();
            return;
          }
          if (opcode === 0x9) {
            this._frame(0xa, payload);
            continue;
          }
          if (opcode === 0x1 && this.onmessage) this.onmessage({ data: payload.toString("utf8") });
        }
      });
    });
  }
  _frame(opcode, data) {
    const payload = Buffer.isBuffer(data) ? data : Buffer.from(data);
    const mask = crypto.randomBytes(4);
    const head = [0x80 | opcode];
    if (payload.length < 126) head.push(0x80 | payload.length);
    else if (payload.length < 65536) head.push(0x80 | 126);
    else head.push(0x80 | 127);
    const parts = [Buffer.from(head)];
    if (payload.length >= 126 && payload.length < 65536) {
      const b = Buffer.alloc(2);
      b.writeUInt16BE(payload.length);
      parts.push(b);
    } else if (payload.length >= 65536) {
      const b = Buffer.alloc(8);
      b.writeBigUInt64BE(BigInt(payload.length));
      parts.push(b);
    }
    parts.push(mask);
    const masked = Buffer.from(payload);
    for (let i = 0; i < masked.length; i++) masked[i] ^= mask[i % 4];
    parts.push(masked);
    this.sock.write(Buffer.concat(parts));
  }
  send(data) {
    this._frame(0x1, data);
  }
  close() {
    this._frame(0x8, Buffer.alloc(0));
    this.sock.end();
  }
}

/* ---------------- minimal CDP client ---------------- */
class CDP {
  constructor(url) {
    this.url = url;
    this.nextId = 1;
    this.pending = new Map();
  }
  connect() {
    return new Promise((resolve, reject) => {
      this.ws = new WS(this.url);
      this.ws.connect().catch(() => {});
      this.ws.onopen = () => resolve();
      this.ws.onerror = (e) => reject(new Error("CDP ws error: " + e.message));
      this.ws.onmessage = (ev) => {
        let msg;
        try {
          msg = JSON.parse(ev.data);
        } catch {
          return;
        }
        const p = this.pending.get(msg.id);
        if (p) {
          this.pending.delete(msg.id);
          p(msg);
        }
      };
    });
  }
  send(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, (msg) => {
        if (msg.error) reject(new Error(`CDP ${method}: ${msg.error.message}`));
        else resolve(msg.result);
      });
      this.ws.send(JSON.stringify({ id, method, params }));
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error(`CDP ${method}: timed out`));
        }
      }, 30000);
    });
  }
  async evaluate(expression) {
    const r = await this.send("Runtime.evaluate", {
      expression,
      returnByValue: true,
    });
    if (r.exceptionDetails) {
      const t = r.exceptionDetails.exception
        ? r.exceptionDetails.exception.description
        : r.exceptionDetails.text;
      throw new Error("page JS threw: " + t);
    }
    return r.result && "value" in r.result ? r.result.value : undefined;
  }
  close() {
    try {
      this.ws.close();
    } catch {
      /* ignore */
    }
  }
}

async function poll(cdp, label, expr, ok, timeoutMs) {
  const start = Date.now();
  let last;
  while (Date.now() - start < timeoutMs) {
    try {
      last = await cdp.evaluate(expr);
      if (ok(last)) return last;
    } catch (e) {
      last = "evaluate error: " + e.message;
    }
    await sleep(2000);
  }
  fail(`${label} timed out after ${timeoutMs / 1000}s (last: ${JSON.stringify(last)})`);
}

/* ---------------- main ---------------- */
(async () => {
  // 0. CDN pre-check: without Pyodide nothing can work; skip loudly.
  console.log("checking Pyodide CDN reachability…");
  try {
    const res = await fetch(PYODIDE_JS, {
      method: "HEAD",
      signal: AbortSignal.timeout(20000),
    });
    if (!res.ok) skip(`CDN unreachable, skipping (HTTP ${res.status})`);
  } catch (e) {
    skip(`CDN unreachable, skipping (${e.message || e})`);
  }
  console.log("CDN reachable.");

  // 1. Proxy setup (only when the environment mandates an upstream proxy).
  const upstreamRaw = process.env.https_proxy || process.env.HTTPS_PROXY || "";
  let forwardProxy = null;
  const chromeFlags = [
    "--headless=new",
    "--no-sandbox",
    "--no-zygote",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--no-first-run",
    "--disable-component-update",
  ];
  if (upstreamRaw) {
    const upstream = new URL(upstreamRaw);
    forwardProxy = await startForwardProxy(upstream);
    const fwdPort = forwardProxy.address().port;
    chromeFlags.push(`--proxy-server=http://127.0.0.1:${fwdPort}`);
    // The sandbox egress proxy MITMs TLS with a CA outside Chrome's trust
    // store; scoped to this test run only.
    chromeFlags.push("--ignore-certificate-errors");
    console.log(`upstream proxy ${upstream.host} -> forward proxy on 127.0.0.1:${fwdPort}`);
  }

  // 2. Serve the playground over http (used unless the browser blocks it).
  const srv = await startServer();
  const srvPort = srv.address().port;
  const httpUrl = `http://127.0.0.1:${srvPort}/index.html`;
  const fileUrl = "file://" + path.join(HERE, "index.html");
  console.log("serving", HERE, "at", httpUrl);

  // 3. Launch headless Chromium with remote debugging.
  const dbgPort = await freePort();
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), "niko-e2e-"));
  const chrome = spawn(
    CHROME,
    [
      ...chromeFlags,
      `--remote-debugging-port=${dbgPort}`,
      `--user-data-dir=${userDataDir}`,
      "about:blank",
    ],
    { stdio: "ignore" }
  );
  const killChrome = () => {
    try {
      chrome.kill("SIGKILL");
    } catch {
      /* ignore */
    }
  };
  const cleanup = () => {
    killChrome();
    srv.close();
    if (forwardProxy) forwardProxy.close();
    try {
      fs.rmSync(userDataDir, { recursive: true, force: true });
    } catch {
      /* ignore */
    }
  };
  process.on("exit", cleanup);
  console.log("launched chromium, devtools on", dbgPort);

  try {
    // 4. Find the page target and connect CDP.
    let wsUrl = null;
    for (let i = 0; i < 30; i++) {
      await sleep(1000);
      try {
        const res = await fetch(`http://127.0.0.1:${dbgPort}/json/list`);
        const targets = await res.json();
        const page = targets.find((t) => t.type === "page");
        if (page && page.webSocketDebuggerUrl) {
          wsUrl = page.webSocketDebuggerUrl;
          break;
        }
      } catch {
        /* not up yet */
      }
    }
    if (!wsUrl) fail("no debuggable page target appeared");
    const cdp = new CDP(wsUrl);
    await cdp.connect();
    console.log("CDP connected.");

    // 5. Navigate. Chromium >= 142 blocks top-level navigation from a fresh
    // tab to 127.0.0.1 (Local Network Access checks); fall back to the
    // file:// page, which the playground explicitly supports.
    let nav = await cdp.send("Page.navigate", { url: httpUrl });
    let usingFile = false;
    const lnaBlocked =
      (nav.errorText || "").includes("BLOCKED_BY_LOCAL_NETWORK_ACCESS_CHECKS");
    if (lnaBlocked) {
      console.log("browser blocked local-network navigation (LNA checks); falling back to file:// page");
      nav = await cdp.send("Page.navigate", { url: fileUrl });
      usingFile = true;
    } else {
      await sleep(4000);
      const loc = await cdp.evaluate("window.location.href").catch(() => "");
      if (String(loc).startsWith("chrome-error://")) {
        console.log("navigation landed on an error page; falling back to file:// page");
        nav = await cdp.send("Page.navigate", { url: fileUrl });
        usingFile = true;
      }
    }
    if ((nav.errorText || "").includes("BLOCKED_BY_LOCAL_NETWORK_ACCESS_CHECKS"))
      fail("browser would not load the page at all: " + nav.errorText);
    console.log("page loading via", usingFile ? "file://" : "http://127.0.0.1");

    // 6. Wait for the playground to be ready (real Pyodide download).
    console.log("waiting for window.__nikoReady (Pyodide ~10-15 MB)…");
    await poll(
      cdp,
      "playground ready",
      "window.__nikoReady === true || window.__nikoLoadError || null",
      (v) => v === true,
      180000
    );
    const loadErr = await cdp.evaluate("window.__nikoLoadError || null");
    if (loadErr) skip(`CDN unreachable, skipping (page reports: ${loadErr})`);
    console.log("playground ready.");

    // 7. Run the fibonacci example; assert 6765 in the output.
    await cdp.evaluate(
      "document.getElementById('editor').value = NIKO_EXAMPLES.fibonacci;" +
        "document.getElementById('run').click();"
    );
    console.log("fibonacci running; waiting for 6765 in output…");
    const out = await poll(
      cdp,
      "fibonacci output",
      "document.getElementById('output').textContent",
      (v) => typeof v === "string" && v.includes("6765"),
      60000
    );
    console.log("output pane:\n" + out);
    const status = await cdp.evaluate("document.getElementById('status').textContent");
    console.log("status after run:", JSON.stringify(status));

    // 8. Broken program must surface a compile error (page stays usable).
    await cdp.evaluate(
      "document.getElementById('editor').value = 'set x to =';" +
        "document.getElementById('run').click();"
    );
    const errText = await poll(
      cdp,
      "compile error",
      "document.getElementById('errors').textContent",
      (v) => typeof v === "string" && v.trim().length > 0,
      60000
    );
    console.log("error pane:\n" + errText);
    const status2 = await cdp.evaluate("document.getElementById('status').textContent");
    if (status2 !== "ready")
      fail(`page not back to ready after compile error (status=${JSON.stringify(status2)})`);

    // 9. Sanity: no stray external references besides the Pyodide CDN.
    const html = fs.readFileSync(path.join(HERE, "index.html"), "utf8");
    const extRefs = [...html.matchAll(/https?:\/\/[^\s"'<>]+/g)].map((m) => m[0]);
    const nonCdn = extRefs.filter(
      (u) => !u.startsWith("https://cdn.jsdelivr.net/pyodide/v0.29.1/")
    );
    if (nonCdn.length) fail("index.html has non-CDN external refs: " + nonCdn.join(", "));
    console.log("no non-CDN external references in index.html.");

    cdp.close();
    console.log("E2E PASS");
    process.exit(0);
  } finally {
    cleanup();
  }
})().catch((e) => fail(e.stack || String(e)));
