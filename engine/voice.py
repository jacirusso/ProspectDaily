"""Voice matching: keep each customer's writing style so the AI drafts sound
like them, not like a robot.

Two sources feed the AI:
  1. A voice PROFILE the customer sets once — a sample of their own writing plus
     tone notes (stored on the customers row).
  2. Voice EXAMPLES — real outreach they've edited/approved (captured from the
     "save my version" action in LeadDaily), used as few-shot guidance.

Lives in the engine so both the daily pipeline and the web app can use it.
"""
import time
import uuid
from typing import Dict, List

from . import db


def profile_for(customer_id: str) -> Dict[str, str]:
    """The customer's voice profile (writing sample + tone notes)."""
    db.init_schema()
    row = db.query_one(
        "SELECT voice_sample, voice_notes FROM customers WHERE id = ?",
        (customer_id,))
    if not row:
        return {"sample": "", "notes": ""}
    return {"sample": (row.get("voice_sample") or "").strip(),
            "notes": (row.get("voice_notes") or "").strip()}


def save_profile(customer_id: str, sample: str, notes: str) -> None:
    db.execute(
        "UPDATE customers SET voice_sample = ?, voice_notes = ?, updated_at = ? "
        "WHERE id = ?",
        ((sample or "").strip(), (notes or "").strip(), int(time.time()),
         customer_id))


def record_example(customer_id: str, kind: str, text: str) -> None:
    """Store a piece of outreach the customer wrote/approved as a voice example."""
    text = (text or "").strip()
    if not text:
        return
    kind = kind if kind in ("email", "linkedin") else "email"
    db.execute(
        "INSERT INTO voice_examples (id, customer_id, kind, text, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (uuid.uuid4().hex, customer_id, kind, text[:4000], int(time.time())))


def examples_for(customer_id: str, limit: int = 3) -> List[Dict[str, str]]:
    """The customer's most recent voice examples, newest first."""
    db.init_schema()
    rows = db.query(
        "SELECT kind, text FROM voice_examples WHERE customer_id = ? "
        "ORDER BY created_at DESC LIMIT ?", (customer_id, limit))
    return [{"kind": r["kind"], "text": r["text"]} for r in rows]


def has_voice(customer_id: str) -> bool:
    p = profile_for(customer_id)
    return bool(p["sample"] or p["notes"] or examples_for(customer_id, 1))
