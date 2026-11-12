import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from dashboard_data import snapshot


ROOT = Path(__file__).resolve().parent
FILES = {"/": ("index.html", "text/html"), "/app.css": ("app.css", "text/css"),
         "/app.js": ("app.js", "application/javascript")}


class LiveData:
    def __init__(self):
        self.current = None
        self.error = False
        self.stop = threading.Event()

    def collect(self):
        while not self.stop.is_set():
            try:
                self.current = snapshot()
                self.error = False
            except Exception as error:
                self.error = True
                print(f"Snapshot error: {type(error).__name__}", file=sys.stderr)
            self.stop.wait(4)


class DashboardHandler(BaseHTTPRequestHandler):
    def send_body(self, status, payload, media_type):
        self.send_response(status)
        self.send_header("Content-Type", media_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (ConnectionResetError, BrokenPipeError):
            pass

    def send_json(self, status, data):
        self.send_body(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json")

    def do_GET(self):
        host = self.headers.get("Host", "")
        path = urlsplit(self.path).path
        expected = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if host not in expected or (path.startswith("/api/") and self.headers.get("Sec-Fetch-Site") == "cross-site"):
            self.send_json(403, {"error": "看板只接受本机访问"})
            return
        if path == "/api/dashboard":
            data = self.server.live.current
            if data is None or self.server.live.error:
                self.send_json(503, {"error": "暂时无法读取员工数据"})
                return
            self.send_json(200, data)
        elif path == "/api/health":
            self.send_json(200, {"service": "codex-employee-dashboard"})
        elif path in FILES:
            filename, kind = FILES[path]
            self.send_body(200, (ROOT / "static" / filename).read_bytes(), kind)
        else:
            self.send_json(404, {"error": "页面不存在"})

    def log_message(self, fmt, *args):
        return


def main():
    parser = argparse.ArgumentParser(description="启动 Codex 研发员工看板。")
    parser.add_argument("--port", default=8767, type=int)
    args = parser.parse_args()
    with ThreadingHTTPServer(("127.0.0.1", args.port), DashboardHandler) as server:
        server.live = LiveData()
        worker = threading.Thread(target=server.live.collect, daemon=True)
        worker.start()
        print(f"Local: http://127.0.0.1:{server.server_port}/", flush=True)
        try:
            server.serve_forever()
        finally:
            server.live.stop.set()
            worker.join(timeout=3)


if __name__ == "__main__":
    main()
