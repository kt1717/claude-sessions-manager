#!/usr/bin/env python3
"""Headless-Chrome driver for csm's web dashboard, spoken directly over the
Chrome DevTools Protocol (CDP) — no Playwright/Puppeteer/chromium-cli needed,
just Python's stdlib + the `websockets` package (pip3 install --user websockets).

Launches Chrome once, executes a small stdin script (one command per line,
chromium-cli-style), then tears Chrome down. Self-contained per invocation —
no long-lived background process to babysit across separate shell calls.

Usage:
    python3 driver.py [--session NAME] [--port PORT] <<'EOF'
    nav http://127.0.0.1:8798/
    wait-for document.querySelector('#cards').children.length > 0
    screenshot loaded
    click-node m:/home/kelly/claude-session-monitor
    screenshot expanded
    quit
    EOF

Screenshots land in sessions/<name>/screenshots/<label>.png next to this
script (default session name: "default"), with screenshot.png symlinked to
the latest one (mirrors chromium-cli's convention).

Commands:
    nav <url>                       navigate and wait for document.readyState=complete
    wait-for <js-boolean-expr>      poll (up to 15s) until the expression is truthy
    sleep <seconds>                 flat pause — last resort, prefer wait-for
    screenshot <label>              save sessions/<name>/screenshots/<label>.png
    click <css-selector>            document.querySelector(sel).click()
    set-checked <selector> <0|1>    checkbox: set .checked and dispatch 'change'
    fill <selector> <text...>       set .value and dispatch 'input' (rest of line is
                                     the value verbatim, including spaces)
    click-node <cy-node-id>         cytoscape: cy.getElementById(id).emit('tap')
                                     (this is what a real click on a graph node does —
                                     Cytoscape nodes are canvas-drawn, not DOM elements,
                                     so a DOM click cannot reach them)
    rightclick-node <cy-node-id>    cytoscape: .emit('cxttap')  (our rename gesture)
    eval <js-expression>            print the JSON-serialized result
    console-errors                  print any console.error(...) calls seen so far
    quit                            close Chrome and exit
"""
import asyncio
import base64
import itertools
import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

try:
    import websockets
except ImportError:
    sys.exit("Missing dependency: pip3 install --user websockets")

HERE = Path(__file__).resolve().parent


def find_chrome():
    for name in ("google-chrome", "google-chrome-stable", "chromium-browser", "chromium"):
        p = shutil.which(name)
        if p:
            return p
    sys.exit("No Chrome/Chromium binary found on PATH")


class CDP:
    """Minimal single-page CDP client: one request/response id space, plus a
    background pump that routes replies to `send()`'s waiter and buffers
    console.error(...) calls for the `console-errors` command."""

    def __init__(self, port, profile_dir):
        self.port = port
        self.profile_dir = profile_dir
        self.proc = None
        self.ws = None
        self._id = itertools.count(1)
        self._pending = {}
        self.console_errors = []

    def launch(self):
        chrome = find_chrome()
        self.proc = subprocess.Popen([
            chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
            "--hide-scrollbars", "--remote-debugging-port=" + str(self.port),
            "--remote-debugging-address=127.0.0.1",
            "--user-data-dir=" + str(self.profile_dir),
            "--window-size=1500,950", "about:blank",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def kill(self):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    async def connect(self, timeout=15):
        deadline = time.time() + timeout
        target = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/list", timeout=1) as r:
                    targets = json.loads(r.read())
                target = next((t for t in targets if t.get("type") == "page"), None)
                if target:
                    break
            except Exception:
                pass
            await asyncio.sleep(0.3)
        if not target:
            raise RuntimeError(f"Chrome DevTools endpoint never came up on port {self.port}")
        self.ws = await websockets.connect(target["webSocketDebuggerUrl"], max_size=None)
        asyncio.create_task(self._pump())
        await self.send("Page.enable")
        await self.send("Runtime.enable")

    async def _pump(self):
        try:
            while True:
                msg = json.loads(await self.ws.recv())
                if "id" in msg:
                    self._pending[msg["id"]] = msg
                elif msg.get("method") == "Runtime.consoleAPICalled" and msg["params"].get("type") == "error":
                    parts = [a.get("value", a.get("description", "")) for a in msg["params"].get("args", [])]
                    self.console_errors.append(" ".join(str(p) for p in parts))
        except websockets.exceptions.ConnectionClosed:
            pass

    async def send(self, method, params=None, timeout=60):
        msg_id = next(self._id)
        await self.ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            if msg_id in self._pending:
                return self._pending.pop(msg_id)
            await asyncio.sleep(0.02)
        raise TimeoutError(f"CDP call {method} timed out")

    async def eval_js(self, expr):
        r = await self.send("Runtime.evaluate", {
            "expression": expr, "returnByValue": True, "awaitPromise": True,
        })
        result = r.get("result", {})
        if "exceptionDetails" in result:
            raise RuntimeError(json.dumps(result["exceptionDetails"]))
        return result.get("result", {}).get("value")


async def run(script_lines, session, port):
    shots_dir = HERE / "sessions" / session / "screenshots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = Path(f"/tmp/csm-driver-profile-{session}-{port}")
    profile_dir.mkdir(parents=True, exist_ok=True)

    cdp = CDP(port, profile_dir)
    cdp.launch()
    try:
        await cdp.connect()
        for lineno, raw_line in enumerate(script_lines, 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            cmd, rest = parts[0], (parts[1] if len(parts) > 1 else "")
            print(f"[{lineno}] {line}", file=sys.stderr)

            if cmd == "nav":
                await cdp.send("Page.navigate", {"url": rest})
                await cdp.eval_js(
                    "new Promise(res => { const c=()=>{ "
                    "document.readyState==='complete' ? res(true) : setTimeout(c,50) }; c(); })"
                )
            elif cmd == "wait-for":
                safe = rest.replace("'", "\\'")
                await cdp.eval_js(
                    "new Promise((res,rej) => { const t0=Date.now(); const c=()=>{ "
                    f"try {{ if ({rest}) return res(true); }} catch(e) {{}} "
                    f"if (Date.now()-t0>15000) return rej('wait-for timed out: {safe}'); "
                    "setTimeout(c,100) }; c(); })"
                )
            elif cmd == "sleep":
                await asyncio.sleep(float(rest))
            elif cmd == "screenshot":
                r = await cdp.send("Page.captureScreenshot", {"format": "png"})
                path = shots_dir / f"{rest}.png"
                path.write_bytes(base64.b64decode(r["result"]["data"]))
                latest = shots_dir / "screenshot.png"
                if latest.exists() or latest.is_symlink():
                    latest.unlink()
                latest.symlink_to(path.name)
                print(f"  -> {path}", file=sys.stderr)
            elif cmd == "click":
                await cdp.eval_js(f"document.querySelector({rest!r}).click()")
            elif cmd == "set-checked":
                sel, val = rest.split(None, 1)
                await cdp.eval_js(
                    f"(()=>{{ const el=document.querySelector({sel!r}); "
                    f"el.checked={str(val.strip() in ('1', 'true')).lower()}; "
                    "el.dispatchEvent(new Event('change')); return true; })()"
                )
            elif cmd == "fill":
                sel, val = rest.split(None, 1)
                await cdp.eval_js(
                    f"(()=>{{ const el=document.querySelector({sel!r}); "
                    f"el.value={val!r}; el.dispatchEvent(new Event('input')); return true; }})()"
                )
            elif cmd == "click-node":
                await cdp.eval_js(f"cy.getElementById({rest!r}).emit('tap')")
            elif cmd == "rightclick-node":
                await cdp.eval_js(f"cy.getElementById({rest!r}).emit('cxttap')")
            elif cmd == "eval":
                print(json.dumps(await cdp.eval_js(rest)), file=sys.stdout)
            elif cmd == "console-errors":
                print(json.dumps(cdp.console_errors), file=sys.stdout)
            elif cmd == "quit":
                break
            else:
                print(f"  ! unknown command: {cmd}", file=sys.stderr)
    finally:
        cdp.kill()
        shutil.rmtree(profile_dir, ignore_errors=True)


def main():
    argv = sys.argv[1:]
    session, port = "default", 9333
    while argv:
        if argv[0] == "--session":
            session, argv = argv[1], argv[2:]
        elif argv[0] == "--port":
            port, argv = int(argv[1]), argv[2:]
        else:
            sys.exit(f"unknown arg: {argv[0]}")
    lines = sys.stdin.read().splitlines()
    asyncio.run(run(lines, session, port))


if __name__ == "__main__":
    main()
