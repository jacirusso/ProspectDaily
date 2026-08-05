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
    db.init_schema()
    rows = db.query("SELECT * FROM customers WHERE status = 'active'")
    for d in rows:
        d["answers"] = json.loads(d.get("answers_json") or "{}")
    return rows


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


def recent_runs(customer_id: str, limit: int = 30) -> List[Dict]:
    db.init_schema()
    return db.query(
        "SELECT * FROM runs WHERE customer_id = ? ORDER BY created_at DESC "
        "LIMIT ?", (customer_id, limit))
