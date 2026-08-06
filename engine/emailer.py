"""Transactional email sending via Resend (stdlib urllib, no SDK).

If RESEND_API_KEY isn't set, emails are written to data/outbox/ instead of sent,
so the app runs (and the welcome series can be tested) without a provider.
"""
import json
import logging
import os
import time
import urllib.request
import urllib.error

from . import config

log = logging.getLogger("emailer")


def _outbox_dir() -> str:
    d = os.path.join(os.path.dirname(config.SQLITE_PATH), "outbox")
    os.makedirs(d, exist_ok=True)
    return d


def send(to: str, subject: str, html: str, reply_to: str = "") -> bool:
    """Send one email. Returns True on success (or on outbox-write in dev)."""
    reply_to = reply_to or config.EMAIL_REPLY_TO
    if not config.RESEND_API_KEY:
        # Dev fallback: write to outbox so nothing is lost / can be inspected.
        fname = f"{int(time.time()*1000)}-{to.replace('@', '_at_')}.html"
        with open(os.path.join(_outbox_dir(), fname), "w") as f:
            f.write(f"<!-- to:{to}\n     subject:{subject}\n"
                    f"     from:{config.EMAIL_FROM} -->\n{html}")
        log.info("[outbox] would email %s: %s", to, subject)
        return True
    body = {"from": config.EMAIL_FROM, "to": [to], "subject": subject, "html": html}
    if reply_to:
        body["reply_to"] = reply_to
    req = urllib.request.Request("https://api.resend.com/emails",
                                 data=json.dumps(body).encode(), method="POST")
    req.add_header("Authorization", f"Bearer {config.RESEND_API_KEY}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read()
        log.info("emailed %s: %s", to, subject)
        return True
    except urllib.error.HTTPError as e:
        log.error("Resend error emailing %s: %s %s", to, e.code,
                  e.read().decode(errors="replace")[:200])
        return False
    except Exception:
        log.exception("email send failed for %s", to)
        return False
