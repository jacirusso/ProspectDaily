"""App data store: users, customer profiles (questionnaire answers + plan), and
a log of daily runs. Backed by the shared db layer -> SQLite locally, Postgres
in production, same code."""
import json
import time
import uuid
from typing import Optional, List, Dict

from engine import db


# --- Users --------------------------------------------------------------
def create_user(email: str, password_hash: str) -> str:
    db.init_schema()
    uid = uuid.uuid4().hex
    now = int(time.time())
    db.transaction([
        ("INSERT INTO users (id, email, password_hash, created_at) "
         "VALUES (?, ?, ?, ?)", (uid, email.lower(), password_hash, now)),
        ("INSERT INTO customers (id, email, created_at, updated_at) "
         "VALUES (?, ?, ?, ?)", (uid, email.lower(), now, now)),
    ])
    return uid


def get_user_by_email(email: str) -> Optional[Dict]:
    db.init_schema()
    return db.query_one("SELECT * FROM users WHERE email = ?", (email.lower(),))


def set_email_verified(user_id: str) -> None:
    db.execute("UPDATE users SET email_verified = 1 WHERE id = ?", (user_id,))


def set_password(user_id: str, password_hash: str) -> None:
    db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
               (password_hash, user_id))


# --- Password reset -----------------------------------------------------
def create_password_reset(user_id: str) -> str:
    import secrets
    db.init_schema()
    token = secrets.token_urlsafe(32)
    db.execute("INSERT INTO password_resets (token, user_id, created_at) "
               "VALUES (?, ?, ?)", (token, user_id, int(time.time())))
    return token


def user_id_for_reset(token: str, max_age: int = 3600):
    """Return the user id for a valid, unused, unexpired token, else None."""
    db.init_schema()
    row = db.query_one(
        "SELECT user_id, created_at, used FROM password_resets WHERE token = ?",
        (token,))
    if not row or row["used"] or (int(time.time()) - int(row["created_at"])) > max_age:
        return None
    return row["user_id"]


def consume_reset(token: str) -> None:
    db.execute("UPDATE password_resets SET used = 1 WHERE token = ?", (token,))


# --- Admin --------------------------------------------------------------
def admin_overview() -> List[Dict]:
    """All customers with their delivered totals, for the admin dashboard."""
    db.init_schema()
    rows = db.query("SELECT * FROM customers ORDER BY created_at DESC")
    for d in rows:
        d["answers"] = json.loads(d.get("answers_json") or "{}")
        t = db.query_one("SELECT COUNT(*) AS n FROM delivered_prospects "
                         "WHERE customer_id = ?", (d["id"],))
        d["total_delivered"] = int(t["n"]) if t else 0
    return rows


# --- Customers ----------------------------------------------------------
def get_customer(customer_id: str) -> Optional[Dict]:
    db.init_schema()
    d = db.query_one("SELECT * FROM customers WHERE id = ?", (customer_id,))
    if not d:
        return None
    d["answers"] = json.loads(d.get("answers_json") or "{}")
    return d


def save_answers(customer_id: str, answers: dict) -> None:
    db.execute("UPDATE customers SET answers_json = ?, updated_at = ? WHERE id = ?",
               (json.dumps(answers), int(time.time()), customer_id))


def update_customer(customer_id: str, **fields) -> None:
    if not fields:
        return
    fields["updated_at"] = int(time.time())
    cols = ", ".join(f"{k} = ?" for k in fields)
    db.execute(f"UPDATE customers SET {cols} WHERE id = ?",
               (*fields.values(), customer_id))


def get_customer_by_stripe_id(stripe_customer_id: str) -> Optional[Dict]:
    if not stripe_customer_id:
        return None
    db.init_schema()
    d = db.query_one("SELECT * FROM customers WHERE stripe_customer_id = ?",
                     (stripe_customer_id,))
    if d:
        d["answers"] = json.loads(d.get("answers_json") or "{}")
    return d


def active_customers() -> List[Dict]:
    """Active customers whose access hasn't expired (0 = no expiry)."""
    db.init_schema()
    rows = db.query(
        "SELECT * FROM customers WHERE status = 'active' "
        "AND (access_expires_at = 0 OR access_expires_at > ?)", (int(time.time()),))
    for d in rows:
        d["answers"] = json.loads(d.get("answers_json") or "{}")
    return rows


def prospects_delivered_since(epoch_start: int) -> int:
    """Count prospects delivered since a time (≈ Apollo credits used)."""
    db.init_schema()
    row = db.query_one(
        "SELECT COUNT(*) AS n FROM delivered_prospects WHERE delivered_at >= ?",
        (epoch_start,))
    return int(row["n"]) if row else 0


def claim_promo(code: str, customer_id: str) -> bool:
    """Atomically claim a single-use promo code. Returns True only for the first
    claim; False if the code was already redeemed by anyone."""
    db.init_schema()
    n = db.execute(
        "INSERT INTO promo_redemptions (code, customer_id, redeemed_at) "
        "VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
        (code, customer_id, int(time.time())))
    return n > 0


# --- Custom promo codes (managed from the admin dashboard) --------------
def create_promo_code(code: str, ordered_per_day: int, days: int,
                      label: str = "") -> bool:
    """Add a promo code. Returns False if the code already exists."""
    db.init_schema()
    code_l = (code or "").strip().lower()
    if not code_l:
        return False
    if not label:
        label = f"Starter — {days} days" if days else "Starter — no expiry"
    n = db.execute(
        "INSERT INTO promo_codes (code, label, ordered_per_day, days, created_at) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
        (code_l, label, int(ordered_per_day or 10), int(days or 0), int(time.time())))
    return n > 0


def get_promo_code(code: str) -> Optional[Dict]:
    """Look up a DB-backed promo code -> {label, ordered_per_day, days} or None."""
    db.init_schema()
    return db.query_one(
        "SELECT label, ordered_per_day, days FROM promo_codes WHERE code = ?",
        ((code or "").strip().lower(),))


def all_promo_codes() -> List[Dict]:
    """All DB promo codes, newest first, each flagged whether it's been redeemed."""
    db.init_schema()
    rows = db.query("SELECT code, label, ordered_per_day, days, created_at "
                    "FROM promo_codes ORDER BY created_at DESC")
    redeemed = {r["code"] for r in db.query("SELECT code FROM promo_redemptions")}
    for r in rows:
        r["redeemed"] = r["code"] in redeemed
    return rows


def delete_promo_code(code: str) -> None:
    db.init_schema()
    db.execute("DELETE FROM promo_codes WHERE code = ?",
               ((code or "").strip().lower(),))


# --- Runs ---------------------------------------------------------------
def log_run(customer_id: str, run_date: str, ordered: int, delivered: int,
            csv_path: str, sheet_url: Optional[str],
            status: str = "ok", error: str = "") -> None:
    db.execute(
        "INSERT INTO runs (id, customer_id, run_date, ordered, delivered, "
        "csv_path, sheet_url, status, error, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (uuid.uuid4().hex, customer_id, run_date, ordered, delivered,
         csv_path, sheet_url, status, error, int(time.time())))


# --- Lifecycle emails ---------------------------------------------------
def email_sent(customer_id: str, email_key: str) -> bool:
    db.init_schema()
    return db.query_one(
        "SELECT 1 AS x FROM sent_emails WHERE customer_id = ? AND email_key = ?",
        (customer_id, email_key)) is not None


def mark_email_sent(customer_id: str, email_key: str) -> None:
    db.execute(
        "INSERT INTO sent_emails (customer_id, email_key, sent_at) "
        "VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
        (customer_id, email_key, int(time.time())))


def all_customers() -> List[Dict]:
    """Every customer (for lifecycle-email sweeps)."""
    db.init_schema()
    rows = db.query("SELECT * FROM customers")
    for d in rows:
        d["answers"] = json.loads(d.get("answers_json") or "{}")
    return rows


def recent_runs(customer_id: str, limit: int = 30) -> List[Dict]:
    db.init_schema()
    return db.query(
        "SELECT * FROM runs WHERE customer_id = ? ORDER BY created_at DESC "
        "LIMIT ?", (customer_id, limit))
