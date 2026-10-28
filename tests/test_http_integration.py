import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import threading
import unittest
from quantscope.interfaces.http import Handler


class LocalHTTP(unittest.TestCase):
    def test_real_http_signal_and_invalid_json(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_port
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            payload = (
                Path(__file__).resolve().parents[1] / "examples/quantlibra.json"
            ).read_bytes()
            conn.request(
                "POST",
                "/api/v1/signals",
                body=payload,
                headers={"Content-Type": "application/json"},
            )
            response = conn.getresponse()
            body = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertEqual(body["model"], "quantlibra")
            conn.close()
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            conn.request("POST", "/api/v1/signals", body=b"{invalid")
            response = conn.getresponse()
            response.read()
            self.assertEqual(response.status, 400)
            conn.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
