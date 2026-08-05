"""The dedupe ledger: a permanent record of every prospect ever delivered to a
given customer. This is what guarantees "no duplicate prospects" -- across days,
across months, forever -- per customer.

Backed by the shared db layer, so it runs on SQLite locally and Postgres in
production with no code changes.
"""
import time
from typing import Set

from . import db


def seen_keys(customer_id: str) -> Set[str]:
    """Every identity signal this customer has already received: the stored
    dedupe_key PLUS email/linkedin forms, so a prospect is caught as a
    duplicate no matter which signal matches."""
    db.init_schema()
    rows = db.query(
        "SELECT dedupe_key, email, linkedin_url FROM delivered_prospects "
        "WHERE customer_id = ?", (customer_id,))
    seen: Set[str] = set()
    for r in rows:
        if r.get("dedupe_key"):
            seen.add(r["dedupe_key"])
        if r.get("email"):
            seen.add("email:" + r["email"].strip().lower())
        if r.get("linkedin_url"):
            seen.add("li:" + r["linkedin_url"].strip().lower().rstrip("/"))
    return seen


def record(customer_id: str, prospects) -> int:
    """Mark prospects as delivered. Returns how many were newly recorded.
    ON CONFLICT DO NOTHING means a re-run can never create a duplicate row."""
    db.init_schema()
    now = int(time.time())
    rows = [
        (customer_id, p.dedupe_key(), p.email, p.linkedin_url,
         p.full_name, p.company, now)
        for p in prospects
    ]
    return db.execute_batch(
        """INSERT INTO delivered_prospects
           (customer_id, dedupe_key, email, linkedin_url, full_name,
            company, delivered_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT DO NOTHING""",
        rows)


def total_delivered(customer_id: str) -> int:
    db.init_schema()
    row = db.query_one(
        "SELECT COUNT(*) AS n FROM delivered_prospects WHERE customer_id = ?",
        (customer_id,))
    return int(row["n"]) if row else 0
