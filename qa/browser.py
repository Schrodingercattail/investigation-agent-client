"""QA-only lightweight Chrome DevTools Protocol driver (test harness).

Drives the real frontend in headless Chrome: navigation, JS evaluation
(clicks, DOM reads), console/network capture, screenshots. No product
code is touched by this module.
"""
import base64
import httpx
import json
import subprocess
import time
import urllib.parse

import websocket

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


class Browser:
    def __init__(self, port=9223):
        self.port = port
        self.proc = None
        self.ws = None
        self.msg_id = 0
        self.console_logs = []
        self.network_failures = []
        self.requests = []

    def start(self, url="http://localhost:3001/"):
        self.proc = subprocess.Popen(
            [CHROME,
             f"--remote-debugging-port={self.port}",
             "--headless=new",
             "--no-first-run", "--no-default-browser-check",
             "--user-data-dir=/tmp/qa-chrome-profile",
             f"--remote-allow-origins=*",
             "--window-size=1500,950",
             "--disable-gpu",
             url],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 15
        target = None
        while time.time() < deadline:
            try:
                tabs = httpx.get(
                    f"http://localhost:{self.port}/json", timeout=2).json()
                page = [t for t in tabs if t.get("type") == "page"]
                if page:
                    target = page[0]
                    break
            except Exception:
                time.sleep(0.4)
        if target is None:
            raise RuntimeError("Chrome devtools endpoint did not come up")
        self.ws_url = target["webSocketDebuggerUrl"]
        self.ws = websocket.create_connection(self.ws_url, timeout=15,
                                              max_size=50 * 1024 * 1024)
        self._enable_listeners()

    def _enable_listeners(self):
        self._send("Runtime.enable")
        self._send("Page.enable")
        self._send("Network.enable")
        # drain the enable responses, then register events via a pump
        self._drain(1.0)

    def _send(self, method, params=None):
        self.msg_id += 1
        mid = self.msg_id
        self.ws.send(json.dumps({"id": mid, "method": method,
                                 "params": params or {}}))
        return mid

    def _recv_until_id(self, mid, timeout=15):
        deadline = time.time() + timeout
        pending = []
        while time.time() < deadline:
            try:
                raw = self.ws.recv()
            except websocket.WebSocketTimeoutException:
                break
            msg = json.loads(raw)
            if msg.get("id") == mid:
                # replay collected events is not needed; we pump continuously
                self._handle_event(msg)
                for p in pending:
                    self._handle_event(p)
                return msg
            if "method" in msg:
                self._handle_event(msg)
            else:
                pending.append(msg)
        return None

    def _handle_event(self, msg):
        method = msg.get("method")
        params = msg.get("params", {})
        if method == "Runtime.consoleAPICalled":
            args = " ".join(
                str(a.get("value", a.get("description", "")))
                for a in params.get("args", []))
            self.console_logs.append(
                f"[{params.get('type')}] {args[:500]}")
        elif method == "Runtime.exceptionThrown":
            exc = params.get("exceptionDetails", {})
            text = exc.get("exception", {}).get(
                "description", exc.get("text", ""))
            self.console_logs.append(f"[EXCEPTION] {str(text)[:500]}")
        elif method == "Network.responseReceived":
            resp = params.get("response", {})
            self.requests.append({
                "url": resp.get("url", ""),
                "status": resp.get("status"),
                "method": params.get("type"),
            })
        elif method == "Network.loadingFailed":
            self.network_failures.append({
                "url": params.get("request", {}).get("url", "") if isinstance(
                    params.get("request"), dict) else "",
                "error": params.get("errorText"),
            })
        elif method == "Network.requestWillBeSent":
            req = params.get("request", {})
            self.requests.append({
                "url": req.get("url", ""),
                "method": req.get("method", ""),
                "body": (req.get("postData") or "")[:500],
            })

    def _drain(self, seconds=0.5):
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                raw = self.ws.recv()
                if raw:
                    self._handle_event(json.loads(raw))
            except websocket.WebSocketTimeoutException:
                break

    def navigate(self, url, settle=2.5):
        mid = self._send("Page.navigate", {"url": url})
        self._recv_until_id(mid)
        time.sleep(settle)
        self._drain(settle * 0.6)

    def eval(self, expression, timeout=15):
        """Evaluate JS, return the JSON value of the expression."""
        mid = self._send("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": bool(timeout and timeout > 1),
        })
        resp = self._recv_until_id(mid, timeout=timeout)
        self._drain(0.2)
        if resp is None:
            raise RuntimeError("evaluate timed out")
        result = resp.get("result", {})
        if result.get("exceptionDetails"):
            detail = result["exceptionDetails"]
            raise RuntimeError(
                "JS exception: "
                + str(detail.get("exception", {}).get(
                    "description", detail.get("text")))[:300])
        return result.get("result", {}).get("value")

    def click(self, selector):
        return self.eval(
            f"(() => {{ const el = document.querySelector({json.dumps(selector)});"
            f" if (!el) return 'NOT_FOUND'; el.click(); return 'CLICKED'; }})()")

    def click_text(self, text, tag="*"):
        return self.eval(
            f"(() => {{ const els=[...document.querySelectorAll({json.dumps(tag)})];"
            f"const el=els.find(e=>e.textContent.trim()==={json.dumps(text)});"
            f"if(!el) return 'NOT_FOUND'; el.click(); return 'CLICKED';}})()")

    def request_body(self, url_fragment):
        """Return the postData of the most recent matching request."""
        for r in reversed(self.requests):
            if url_fragment in r.get("url", "") and r.get("body"):
                return r["body"]
        return None

    def dom_text(self, selector="body"):
        return self.eval(
            f"document.querySelector({json.dumps(selector)})?.innerText ?? ''")

    def screenshot(self, path):
        mid = self._send("Page.captureScreenshot",
                         {"format": "png", "captureBeyondViewport": False})
        resp = self._recv_until_id(mid)
        data = resp.get("result", {}).get("data")
        if data:
            with open(path, "wb") as f:
                f.write(base64.b64decode(data))
            return path
        return None

    def close(self):
        try:
            if self.ws:
                self.ws.close()
            if self.proc:
                self.proc.terminate()
        except Exception:
            pass
