"""LeadDaily — the CRM add-on's data layer.

Turns delivered prospects (captured in `report_prospects` at delivery time) into
worked leads: a pipeline with stages, an activity timeline, and tasks. Same db
layer as everything else, so SQLite locally and Postgres in production.

Everything here is scoped by customer_id — a customer can only ever see or touch
their own leads, prospects, activities, and tasks.
"""
import json
import time
import uuid
from typing import Dict, List, Optional

from engine import db

# Pipeline stages, in board order. Terminal stages (won/lost) sort last.
STAGES = [
    {"key": "new", "label": "New"},
    {"key": "contacted", "label": "Contacted"},
    {"key": "replied", "label": "Replied"},
    {"key": "meeting", "label": "Meeting"},
    {"key": "won", "label": "Won"},
    {"key": "lost", "label": "Lost"},
]
STAGE_KEYS = [s["key"] for s in STAGES]
STAGE_LABELS = {s["key"]: s["label"] for s in STAGES}

# Activity kinds we log to the timeline.
ACTIVITY_KINDS = {"note", "email", "call", "meeting", "stage", "created"}


def _now() -> int:
    return int(time.time())


def _ordered_id() -> str:
    """A unique id that sorts chronologically by lexical/DESC order — a
    millisecond prefix + random suffix. Keeps the activity timeline in true
    insertion order even when several land in the same second."""
    return f"{int(time.time() * 1000):013d}-{uuid.uuid4().hex[:8]}"


# --- Delivered prospects (the "Add to CRM" source) ----------------------
def list_report_prospects(customer_id: str, limit: int = 100) -> List[Dict]:
    """Recently delivered prospects for this customer, newest first, each
    flagged whether it's already been added to the CRM."""
    db.init_schema()
    rows = db.query(
        "SELECT id, run_date, full_name, title, company, email, added_lead_id "
        "FROM report_prospects WHERE customer_id = ? "
        "ORDER BY created_at DESC, id DESC LIMIT ?", (customer_id, limit))
    for r in rows:
        r["added"] = bool(r.get("added_lead_id"))
    return rows


def get_report_prospect(customer_id: str, prospect_id: str) -> Optional[Dict]:
    db.init_schema()
    r = db.query_one(
        "SELECT * FROM report_prospects WHERE id = ? AND customer_id = ?",
        (prospect_id, customer_id))
    if r:
        r["data"] = json.loads(r.get("data_json") or "{}")
    return r


def prospect_counts(customer_id: str) -> Dict[str, int]:
    db.init_schema()
    total = db.query_one("SELECT COUNT(*) AS n FROM report_prospects "
                         "WHERE customer_id = ?", (customer_id,))
    added = db.query_one("SELECT COUNT(*) AS n FROM report_prospects "
                         "WHERE customer_id = ? AND added_lead_id <> ''",
                         (customer_id,))
    t = int(total["n"]) if total else 0
    a = int(added["n"]) if added else 0
    return {"total": t, "added": a, "available": t - a}


# --- Leads --------------------------------------------------------------
def add_lead_from_prospect(customer_id: str, prospect_id: str) -> Optional[str]:
    """Create a lead from a delivered prospect, copying every field. Idempotent:
    if this prospect was already added, returns the existing lead id and does not
    create a second lead. Returns the lead id, or None if the prospect is unknown."""
    rp = get_report_prospect(customer_id, prospect_id)
    if not rp:
        return None
    if rp.get("added_lead_id"):
        return rp["added_lead_id"]
    d = rp.get("data") or {}
    lead_id = uuid.uuid4().hex
    now = _now()
    db.execute(
        """INSERT INTO leads
           (id, customer_id, source_prospect_id, full_name, title, company,
            email, phone, linkedin_url, website, industry, fit_reason,
            intro_subject, intro_body, linkedin_message, stage, value,
            created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', 0, ?, ?)""",
        (lead_id, customer_id, prospect_id,
         d.get("full_name") or rp.get("full_name") or "",
         d.get("title") or "", d.get("company") or "", d.get("email") or "",
         d.get("phone") or "", d.get("linkedin_url") or "",
         d.get("website") or "", d.get("industry") or "",
         d.get("fit_reason") or "", d.get("intro_email_subject") or "",
         d.get("intro_email_body") or "", d.get("linkedin_message") or "",
         now, now))
    db.execute("UPDATE report_prospects SET added_lead_id = ? "
               "WHERE id = ? AND customer_id = ?",
               (lead_id, prospect_id, customer_id))
    add_activity(customer_id, lead_id, "created", "Added to CRM from daily report.")
    return lead_id


def list_leads(customer_id: str) -> List[Dict]:
    db.init_schema()
    return db.query(
        "SELECT * FROM leads WHERE customer_id = ? ORDER BY updated_at DESC",
        (customer_id,))


def leads_by_stage(customer_id: str) -> Dict[str, List[Dict]]:
    """All leads grouped into the pipeline columns (every stage key present)."""
    grouped: Dict[str, List[Dict]] = {k: [] for k in STAGE_KEYS}
    for lead in list_leads(customer_id):
        grouped.setdefault(lead.get("stage") or "new", []).append(lead)
    return grouped


def stage_counts(customer_id: str) -> Dict[str, int]:
    db.init_schema()
    rows = db.query(
        "SELECT stage, COUNT(*) AS n FROM leads WHERE customer_id = ? "
        "GROUP BY stage", (customer_id,))
    counts = {k: 0 for k in STAGE_KEYS}
    for r in rows:
        counts[r["stage"]] = int(r["n"])
    counts["open"] = sum(counts[k] for k in STAGE_KEYS if k not in ("won", "lost"))
    counts["total"] = sum(counts[k] for k in STAGE_KEYS)
    return counts


def get_lead(customer_id: str, lead_id: str) -> Optional[Dict]:
    db.init_schema()
    return db.query_one(
        "SELECT * FROM leads WHERE id = ? AND customer_id = ?",
        (lead_id, customer_id))


def _owns_lead(customer_id: str, lead_id: str) -> bool:
    return db.query_one("SELECT 1 AS x FROM leads WHERE id = ? AND customer_id = ?",
                        (lead_id, customer_id)) is not None


def update_lead_stage(customer_id: str, lead_id: str, stage: str) -> bool:
    if stage not in STAGE_KEYS or not _owns_lead(customer_id, lead_id):
        return False
    lead = get_lead(customer_id, lead_id)
    if lead and lead.get("stage") == stage:
        return True
    db.execute("UPDATE leads SET stage = ?, updated_at = ? "
               "WHERE id = ? AND customer_id = ?",
               (stage, _now(), lead_id, customer_id))
    add_activity(customer_id, lead_id, "stage",
                 f"Moved to {STAGE_LABELS.get(stage, stage)}.")
    return True


def update_lead_value(customer_id: str, lead_id: str, value: int) -> bool:
    if not _owns_lead(customer_id, lead_id):
        return False
    db.execute("UPDATE leads SET value = ?, updated_at = ? "
               "WHERE id = ? AND customer_id = ?",
               (int(value or 0), _now(), lead_id, customer_id))
    return True


def update_lead_draft(customer_id: str, lead_id: str, **fields) -> bool:
    """Update a lead's saved outreach draft (intro_subject / intro_body /
    linkedin_message) after the customer edits it to their voice."""
    allowed = {"intro_subject", "intro_body", "linkedin_message"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields or not _owns_lead(customer_id, lead_id):
        return False
    cols = ", ".join(f"{k} = ?" for k in fields)
    db.execute(f"UPDATE leads SET {cols}, updated_at = ? "
               "WHERE id = ? AND customer_id = ?",
               (*fields.values(), _now(), lead_id, customer_id))
    return True


def delete_lead(customer_id: str, lead_id: str) -> None:
    """Remove a lead and its activities/tasks, and free its source prospect so
    it can be re-added later."""
    if not _owns_lead(customer_id, lead_id):
        return
    db.execute("UPDATE report_prospects SET added_lead_id = '' "
               "WHERE customer_id = ? AND added_lead_id = ?",
               (customer_id, lead_id))
    db.execute("DELETE FROM lead_activities WHERE customer_id = ? AND lead_id = ?",
               (customer_id, lead_id))
    db.execute("DELETE FROM lead_tasks WHERE customer_id = ? AND lead_id = ?",
               (customer_id, lead_id))
    db.execute("DELETE FROM leads WHERE id = ? AND customer_id = ?",
               (lead_id, customer_id))


# --- Activity timeline --------------------------------------------------
def add_activity(customer_id: str, lead_id: str, kind: str, body: str) -> None:
    kind = kind if kind in ACTIVITY_KINDS else "note"
    db.execute(
        "INSERT INTO lead_activities (id, customer_id, lead_id, kind, body, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (_ordered_id(), customer_id, lead_id, kind, (body or "").strip(), _now()))
    # Any logged touch bumps the lead so it sorts to the top of the board.
    db.execute("UPDATE leads SET updated_at = ? WHERE id = ? AND customer_id = ?",
               (_now(), lead_id, customer_id))


def lead_activities(customer_id: str, lead_id: str) -> List[Dict]:
    db.init_schema()
    return db.query(
        "SELECT * FROM lead_activities WHERE customer_id = ? AND lead_id = ? "
        "ORDER BY created_at DESC, id DESC", (customer_id, lead_id))


# --- Tasks --------------------------------------------------------------
def add_task(customer_id: str, lead_id: str, title: str, due_at: int = 0) -> None:
    if not _owns_lead(customer_id, lead_id):
        return
    db.execute(
        "INSERT INTO lead_tasks (id, customer_id, lead_id, title, due_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (uuid.uuid4().hex, customer_id, lead_id, (title or "").strip(),
         int(due_at or 0), _now()))


def lead_tasks(customer_id: str, lead_id: str) -> List[Dict]:
    db.init_schema()
    # Open tasks first (by due date), then completed.
    return db.query(
        "SELECT * FROM lead_tasks WHERE customer_id = ? AND lead_id = ? "
        "ORDER BY done ASC, "
        "CASE WHEN due_at = 0 THEN 1 ELSE 0 END, due_at ASC, created_at ASC",
        (customer_id, lead_id))


def complete_task(customer_id: str, task_id: str, done: bool = True) -> None:
    db.execute("UPDATE lead_tasks SET done = ? WHERE id = ? AND customer_id = ?",
               (1 if done else 0, task_id, customer_id))


def delete_task(customer_id: str, task_id: str) -> None:
    db.execute("DELETE FROM lead_tasks WHERE id = ? AND customer_id = ?",
               (task_id, customer_id))


def tasks_due(customer_id: str, until: Optional[int] = None) -> List[Dict]:
    """Open tasks due on/before `until` (default: end of today, local server
    time), plus any overdue — with their lead's name/company attached. This is
    the "Due today" view."""
    db.init_schema()
    if until is None:
        # End of today in server-local time.
        lt = time.localtime()
        end = time.struct_time((lt.tm_year, lt.tm_mon, lt.tm_mday, 23, 59, 59,
                                lt.tm_wday, lt.tm_yday, lt.tm_isdst))
        until = int(time.mktime(end))
    rows = db.query(
        "SELECT t.id AS id, t.lead_id AS lead_id, t.title AS title, "
        "t.due_at AS due_at, t.created_at AS created_at, "
        "l.full_name AS lead_name, l.company AS lead_company "
        "FROM lead_tasks t JOIN leads l ON l.id = t.lead_id "
        "WHERE t.customer_id = ? AND t.done = 0 AND t.due_at > 0 AND t.due_at <= ? "
        "ORDER BY t.due_at ASC", (customer_id, until))
    return rows


def open_task_count(customer_id: str) -> int:
    db.init_schema()
    row = db.query_one("SELECT COUNT(*) AS n FROM lead_tasks "
                       "WHERE customer_id = ? AND done = 0", (customer_id,))
    return int(row["n"]) if row else 0


def due_today_count(customer_id: str) -> int:
    return len(tasks_due(customer_id))
