"""PDF rendering via a small dedicated render service.

WeasyPrint needs system libraries (Pango, cairo) that the main app's runtime
can't install, so rendering lives in a separate container service (see
pdfservice/). This module just POSTs the report HTML to it and gets PDF bytes
back. If PDF_SERVICE_URL isn't set — or the service is unreachable — callers
fall back to the Google Doc format, so this is always safe.
"""
import logging
import urllib.request
import urllib.error

from . import config

log = logging.getLogger("pdf")


def available() -> bool:
    return bool(config.PDF_SERVICE_URL)


def render_pdf(html: str) -> bytes:
    """POST HTML to the render service, return PDF bytes. Raises on failure so
    the caller can fall back to the Doc format."""
    base = config.PDF_SERVICE_URL.rstrip("/")
    if not base.startswith("http"):
        base = "https://" + base
    req = urllib.request.Request(base + "/render", data=html.encode("utf-8"),
                                 method="POST")
    req.add_header("Content-Type", "text/html; charset=utf-8")
    if config.PDF_SERVICE_SECRET:
        req.add_header("X-Secret", config.PDF_SERVICE_SECRET)
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read()
