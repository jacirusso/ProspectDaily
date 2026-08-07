"""Tiny HTML->PDF render service (WeasyPrint).

Runs in its own container (see Dockerfile) because WeasyPrint needs system
libraries the main app's runtime can't install. The main app POSTs report HTML
to POST /render and gets a PDF back. Protected by a shared secret header so
only our own services can call it.

  GET  /healthz  -> "ok"
  POST /render   -> PDF bytes  (header X-Secret must match PDF_SERVICE_SECRET)
"""
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SECRET = os.environ.get("PDF_SERVICE_SECRET", "")
MAX_BYTES = 8 * 1024 * 1024   # 8MB HTML cap


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body=b"", ctype="text/plain"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        if self.path == "/healthz":
            self._send(200, b"ok")
        else:
            self._send(404, b"not found")

    def do_POST(self):
        if self.path != "/render":
            return self._send(404, b"not found")
        if SECRET and self.headers.get("X-Secret") != SECRET:
            return self._send(401, b"unauthorized")
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_BYTES:
            return self._send(413, b"bad length")
        html = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            from weasyprint import HTML
            pdf = HTML(string=html).write_pdf()
        except Exception as e:  # noqa: BLE001
            return self._send(500, ("render error: " + str(e))[:500].encode())
        self._send(200, pdf, ctype="application/pdf")

    def log_message(self, *args):
        pass  # quiet


def main():
    port = int(os.environ.get("PORT", "8000"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
