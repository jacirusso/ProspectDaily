"""The AI support agent: a Claude-backed assistant that answers questions and
captures suggestions on the marketing site and inside the app. Conversations are
stored so the operator gets a daily digest.
"""
import time
import uuid
from typing import Dict, List, Optional

from engine import db, config, ai

MAX_MSG_CHARS = 2000        # per user message
MAX_TURNS = 40              # messages per conversation before we wrap up
HISTORY_TURNS = 20          # how much history we send to Claude


# --- What the agent knows and how it behaves ---------------------------
_PRODUCT = """ProspectDaily delivers fresh, verified B2B prospects to a customer's
own Google Drive folder every weekday. The customer sets a target audience (the
fastest way is to enter their website and we build it for them). Each prospect
comes with verified email, phone, LinkedIn, a company profile, and a short reason
they fit. As a BONUS, each also includes a draft intro email and a draft LinkedIn
note the customer can edit to their own voice. The verified data is the core
value; the drafts are a starting point, not the product.

Volume plans (billed monthly, or annually for two months free):
- Starter: 10 prospects/weekday, $289/mo (or $2,890/yr, about $241/mo)
- Growth: 20 prospects/weekday, $499/mo (or $4,990/yr, about $416/mo)
- Pro: 40 prospects/weekday, $999/mo (or $9,990/yr, about $832/mo)
Cancel anytime from the dashboard. Reports arrive every weekday morning."""

_RULES = """You are ProspectDaily's friendly support and sales assistant.
- Be warm, concise, and genuinely helpful. Short answers. Sound like a real
  person. Never use em-dashes or en-dashes; use a period or comma.
- Answer questions about what ProspectDaily is, how it works, setup, pricing,
  and billing using only the facts above. Do NOT invent features, integrations,
  numbers, or promises. If you do not know, say so plainly.
- You cannot take account actions (changing plans, issuing refunds, editing
  someone's account, or sending their reports). For anything like that, or a bug
  or complaint, collect the person's email if you do not already have it and tell
  them the team will follow up. Do not promise a specific timeline.
- Encourage suggestions and feedback, and thank people for them.
- Never ask for or accept passwords, card numbers, or other sensitive credentials.
- If someone is ready to start, point them to the Get started / sign up flow. If
  they are stuck in setup, walk them through entering their website as step one."""


def _system(context: str, customer: Optional[Dict]) -> str:
    who = "You are talking to a website visitor (not logged in)."
    if customer:
        plan = customer.get("plan") or "no plan yet"
        status = customer.get("status") or "unknown"
        who = (f"You are talking to a LOGGED-IN customer. Their email is "
               f"{customer.get('email','')}, plan: {plan}, status: {status}. "
               f"You can reference their account context but still cannot take "
               f"actions on it.")
    return f"{_RULES}\n\nPRODUCT FACTS:\n{_PRODUCT}\n\nCONTEXT:\n{who}"


# --- Conversation storage ----------------------------------------------
def _get_or_create(session_id: str, context: str, customer: Optional[Dict]) -> Dict:
    db.init_schema()
    session_id = (session_id or uuid.uuid4().hex)[:64]
    conv = db.query_one(
        "SELECT * FROM chat_conversations WHERE session_id = ?", (session_id,))
    if conv:
        return conv
    cid = uuid.uuid4().hex
    now = int(time.time())
    db.execute(
        "INSERT INTO chat_conversations (id, session_id, context, customer_id, "
        "email, created_at, last_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (cid, session_id, context if context in ("site", "app") else "site",
         (customer or {}).get("id", "") or "",
         (customer or {}).get("email", "") or "", now, now))
    return db.query_one("SELECT * FROM chat_conversations WHERE id = ?", (cid,))


def _add_message(conversation_id: str, role: str, content: str) -> None:
    db.execute(
        "INSERT INTO chat_messages (id, conversation_id, role, content, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (uuid.uuid4().hex, conversation_id, role, content, int(time.time())))
    db.execute("UPDATE chat_conversations SET msg_count = msg_count + 1, last_at = ? "
               "WHERE id = ?", (int(time.time()), conversation_id))


def _history(conversation_id: str, limit: int = HISTORY_TURNS) -> List[Dict]:
    rows = db.query(
        "SELECT role, content FROM chat_messages WHERE conversation_id = ? "
        "ORDER BY created_at DESC, id DESC LIMIT ?", (conversation_id, limit))
    rows.reverse()
    return [{"role": r["role"], "content": r["content"]} for r in rows]


# --- The public entry point --------------------------------------------
def handle(session_id: str, message: str, context: str,
           customer: Optional[Dict]) -> Dict:
    """Take a user message, store it, get Claude's reply, store + return it."""
    message = (message or "").strip()[:MAX_MSG_CHARS]
    if not message:
        return {"reply": "What can I help you with?", "session_id": session_id}
    conv = _get_or_create(session_id, context, customer)
    if conv["msg_count"] >= MAX_TURNS * 2:
        return {"reply": "We've covered a lot here. For anything more, email "
                "the team at jaci@brandstateu.com and they'll take great care of you.",
                "session_id": conv["session_id"]}
    _add_message(conv["id"], "user", message)
    try:
        history = _history(conv["id"])
        text = ai.anthropic_chat(_system(context, customer), history, max_tokens=600)
        if not text:
            raise RuntimeError("empty reply")
    except Exception:
        text = ("Sorry, I hit a snag just now. Please try again, or email the team "
                "at jaci@brandstateu.com and they'll help you out.")
    _add_message(conv["id"], "assistant", text)
    return {"reply": text, "session_id": conv["session_id"]}


# --- Daily digest to the operator --------------------------------------
def _esc(s: str) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _transcript(convs: List[Dict]) -> str:
    out = []
    for c in convs:
        who = c["email"] or f"visitor ({c['context']})"
        out.append(f"=== {who} [{c['context']}] ===")
        rows = db.query("SELECT role, content FROM chat_messages "
                        "WHERE conversation_id = ? ORDER BY created_at, id",
                        (c["id"],))
        for m in rows:
            out.append(f"{m['role'].upper()}: {m['content']}")
        out.append("")
    return "\n".join(out)


def send_daily_digest(hours: int = 24) -> Dict:
    """Summarize the last `hours` of chats and email the operator. No email when
    there were no conversations. Called from the daily cron."""
    db.init_schema()
    since = int(time.time()) - hours * 3600
    convs = db.query("SELECT * FROM chat_conversations WHERE last_at >= ? "
                     "ORDER BY last_at DESC", (since,))
    if not convs:
        return {"conversations": 0, "sent": False}
    transcript = _transcript(convs)

    summary_html = "<p><em>Summary unavailable; see transcripts below.</em></p>"
    try:
        s = ai.anthropic_chat(
            "You summarize a day of customer support chats for the business owner. "
            "Be brief and practical. Never use em-dashes.",
            [{"role": "user", "content":
              "Summarize today's ProspectDaily support chats. Give: (1) a 2 to 3 "
              "sentence overview, (2) the common questions or themes, (3) any "
              "suggestions or feature requests, (4) anyone who needs a follow-up "
              "and their email if they gave one. Transcripts:\n\n"
              + transcript[:12000]}],
            max_tokens=700)
        if s:
            summary_html = "<p>" + _esc(s).replace("\n", "<br>") + "</p>"
    except Exception:
        pass

    body = (f"<p><strong>{len(convs)}</strong> conversation(s) in the last "
            f"{hours} hours.</p><h3>Summary</h3>{summary_html}"
            f"<h3>Transcripts</h3>"
            f"<pre style='white-space:pre-wrap;font-size:13px;color:#334155'>"
            f"{_esc(transcript[:20000])}</pre>")
    try:
        from web import emails
        emails.alert_operator(
            f"ProspectDaily chat digest: {len(convs)} conversation(s)", body)
    except Exception:
        return {"conversations": len(convs), "sent": False}
    return {"conversations": len(convs), "sent": True}
