#!/usr/bin/env python3
"""Stub health endpoint for molecule: answers 200 on every path."""
import http.server


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


http.server.ThreadingHTTPServer(("127.0.0.1", 18080), H).serve_forever()
