#!/usr/bin/env node
/* Alpha 29 playground end-to-end test.
 *
 * Serves examples/playground/ over HTTP, launches real headless Chromium,
 * drives it via CDP (WebSocket), waits for window.__nikoReady, runs the
 * fibonacci example in-page and asserts `6765` lands in the output pane,
 * then checks a broken program surfaces a compile error in the error pane.
 *
 *   node e2e.mjs            # run from examples/playground/
 *
 * Exit codes: 0 = pass; 2 = Pyodide CDN unreachable (skip, NOT a pass);
 * anything else = real failure with a message on stderr.
 *
 * Environment notes (verified): /opt/meta-chromium/chrome is Chromium 152;
 * --dump-dom is broken in this build (empty output) but
 * --remote-debugging-port works, so CDP is used for everything.
 */
"use strict";

import { spawn } from "node:child_process";
import http from "node:http";
import net from "node:net";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const CHROME = "/opt/meta-chromium/chrome";
const PYODIDE_JS =
  "https://cdn.jsdelivr.net/pyodide/v0.29.1/full/pyodide.js";
const HERE = path.dirname(fileURLToPath(import.meta.url));

const fail = (msg) => {
  console.error("E2E FAIL:", msg);
  process.exit(1);
};
const skip = (msg) => {
  console.error("E2E SKIP:", msg);
  process.exit(2);
};

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
          res.writeHead(403); res.end("forbidden"); return;
        }
        fs.readFile(file, (err, data) => {
          if (err) { res.writeHead(404); res.end("not found"); return; }
          res.writeHead(200, {
            "Content-Type": MIME[path.extname(file)] || "application/octet-stream",
          });
          res.end(data);
        });
      } catch {
        res.writeHead(500); res.end("error");
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

/* ---------------- minimal CDP client ---------------- */
class CDP {
  constructor(url) {
    this.url = url;
    this.nextId = 1;
    this.pending = new Map();
  }
  connect() {
    return new Promise((resolve, reject) => {
      this.ws = new WebSocket(this.url);
      this.ws.onopen = () => resolve();
      this.ws.onerror = (e) => reject(new Error("CDP ws error: " + e.message));
      this.ws.onmessage = (ev) => {
        let msg;
        try { msg = JSON.parse(ev.data); } catch { return; }
        const p = this.pending.get(msg.id);
        if (p) { this.pending.delete(msg.id); p(msg); }
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
  close() { try { this.ws.close(); } catch { /* ignore */ } }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

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

  // 1. Serve the playground.
  const srv = await startServer();
  const srvPort = srv.address().port;
  const pageUrl = `http://127.0.0.1:${srvPort}/index.html`;
  console.log("serving", HERE, "at", pageUrl);

  // 2. Launch headless Chromium with remote debugging.
  // NOTE: the page URL is passed as the startup page instead of about:blank:
  // Chromium 152's Local Network Access checks block a top-level navigation
  // from about:blank to 127.0.0.1 (net::ERR_BLOCKED_BY_LOCAL_NETWORK_ACCESS_CHECKS),
  // so starting at about:blank + Page.navigate can never commit. Starting at
  // the page URL directly is not document-initiated and loads fine; we still
  // issue Page.navigate below for a fresh load.
  const dbgPort = await freePort();
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), "niko-e2e-"));
  const chrome = spawn(
    CHROME,
    [
      "--headless=new",
      "--no-sandbox",
      "--no-zygote",
      "--disable-gpu",
      "--disable-dev-shm-usage",
      "--no-first-run",
      "--disable-component-update",
      `--remote-debugging-port=${dbgPort}`,
      `--user-data-dir=${userDataDir}`,
      pageUrl,
    ],
    { stdio: "ignore" }
  );
  const killChrome = () => { try { chrome.kill("SIGKILL"); } catch { /* ignore */ } };
  process.on("exit", () => { killChrome(); srv.close(); });
  console.log("launched chromium, devtools on", dbgPort);

  try {
    // 3. Find the page target and connect CDP.
    let wsUrl = null;
    for (let i = 0; i < 30; i++) {
      await sleep(1000);
      try {
        const res = await fetch(`http://127.0.0.1:${dbgPort}/json/list`);
        const targets = await res.json();
        const page = targets.find((t) => t.type === "page");
        if (page && page.webSocketDebuggerUrl) { wsUrl = page.webSocketDebuggerUrl; break; }
      } catch { /* not up yet */ }
    }
    if (!wsUrl) fail("no debuggable page target appeared");
    const cdp = new CDP(wsUrl);
    await cdp.connect();
    console.log("CDP connected.");

    // 4. Navigate and wait for the playground to be ready.
    await cdp.send("Page.navigate", { url: pageUrl });
    console.log("navigated; waiting for window.__nikoReady (Pyodide ~10-15 MB)…");
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

    // 5. Run the fibonacci example; assert 6765 in the output.
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

    // 6. Broken program must surface a compile error (page stays usable).
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
    if (status2 !== "ready") fail(`page not back to ready after compile error (status=${JSON.stringify(status2)})`);

    // 7. Sanity: no stray external references besides the Pyodide CDN.
    const html = fs.readFileSync(path.join(HERE, "index.html"), "utf8");
    const extRefs = [...html.matchAll(/https?:\/\/[^\s"'<>]+/g)].map((m) => m[0]);
    const nonCdn = extRefs.filter((u) => !u.startsWith("https://cdn.jsdelivr.net/pyodide/v0.29.1/"));
    if (nonCdn.length) fail("index.html has non-CDN external refs: " + nonCdn.join(", "));
    console.log("no non-CDN external references in index.html.");

    cdp.close();
    console.log("E2E PASS");
    process.exit(0);
  } finally {
    killChrome();
    srv.close();
    try { fs.rmSync(userDataDir, { recursive: true, force: true }); } catch { /* ignore */ }
  }
})().catch((e) => fail(e.stack || String(e)));
