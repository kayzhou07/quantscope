"""本地只读/研究HTTP接口，供Web和未来移动端共用。只绑定127.0.0.1。"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from quantscope.core import MODELS
from quantscope.engine import evaluate


def dispatch(method, path, payload):
    if method == "GET" and path == "/health":
        return 200, {"status": "ok", "mode": "offline_research"}
    if method == "GET" and path == "/api/v1/models":
        return 200, {"models": MODELS}
    if method == "POST" and path == "/api/v1/signals":
        try:
            if not isinstance(payload, dict):
                raise ValueError("请求必须为JSON对象")
            signal = evaluate(payload)
            return (
                200 if signal.status in ("ok", "no_candidates") else 422
            ), signal.to_dict()
        except (ValueError, TypeError) as e:
            return 400, {"error": str(e)}
    return 404, {"error": "route_not_found"}


class Handler(BaseHTTPRequestHandler):
    def handle_request(self):
        try:
            raw = self.headers.get("Content-Length", "0")
            size = int(raw)
            if size < 0 or size > 4_000_000:
                raise ValueError("请求体超限")
            payload = (
                json.loads(self.rfile.read(size)) if self.command == "POST" else None
            )
            status, result = dispatch(self.command, self.path, payload)
        except (ValueError, UnicodeError):
            status, result = 400, {"error": "invalid_json_request"}
        body = json.dumps(result, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    do_GET = handle_request
    do_POST = handle_request

    def log_message(self, *args):
        pass


def serve(port=8765):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(
        "QuantScope offline research API: http://127.0.0.1:" + str(server.server_port),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
