"""Tiny database layer that speaks BOTH SQLite (local dev) and Postgres
(production). Pick the backend with the DATABASE_URL env var:

  - unset            -> SQLite file at config.SQLITE_PATH
  - postgres://...   -> Postgres via psycopg

All SQL is written with `?` placeholders and `ON CONFLICT DO NOTHING`, both of
which work on SQLite >= 3.24 and Postgres, so the same statements run everywhere.
Placeholders are rewritten to `%s` for Postgres automatically.
"""
import os
import sqlite3
from typing import List, Optional, Sequence, Tuple

from . import config


def is_postgres() -> bool:
    return config.DATABASE_URL.strip().lower().startswith(
        ("postgres://", "postgresql://"))


def _connect_raw():
    if is_postgres():
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(config.DATABASE_URL, row_factory=dict_row)
    os.makedirs(os.path.dirname(config.SQLITE_PATH), exist_ok=True)
    conn = sqlite3.connect(config.SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _sql(query: str) -> str:
    return query.replace("?", "%s") if is_postgres() else query


# --- read ---------------------------------------------------------------
def query(sql: str, params: Sequence = ()) -> List[dict]:
    conn = _connect_raw()
    try:
        cur = conn.execute(_sql(sql), tuple(params))
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def query_one(sql: str, params: Sequence = ()) -> Optional[dict]:
    rows = query(sql, params)
    return rows[0] if rows else None


# --- write --------------------------------------------------------------
def execute(sql: str, params: Sequence = ()) -> int:
    conn = _connect_raw()
    try:
        cur = conn.execute(_sql(sql), tuple(params))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def execute_batch(sql: str, rows: Sequence[Sequence]) -> int:
    """Run the same statement for many parameter tuples in one transaction.
    Returns the total number of affected rows."""
    conn = _connect_raw()
    total = 0
    try:
        stmt = _sql(sql)
        for params in rows:
            cur = conn.execute(stmt, tuple(params))
            total += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        conn.commit()
        return total
    finally:
        conn.close()


def transaction(statements: Sequence[Tuple[str, Sequence]]) -> None:
    """Run several (sql, params) statements atomically."""
    conn = _connect_raw()
    try:
        for sql, params in statements:
            conn.execute(_sql(sql), tuple(params))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# --- schema -------------------------------------------------------------
def init_schema() -> None:
    """Create all tables if they don't exist. Idempotent; safe to call often."""
    ts = "BIGINT" if is_postgres() else "INTEGER"   # epoch seconds
    ic = "INTEGER"
    ddl = [
        f"""CREATE TABLE IF NOT EXISTS users (
            id            TEXT PRIMARY KEY,
            email         TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            email_verified {ic} NOT NULL DEFAULT 0,
            created_at    {ts} NOT NULL
        )""",
        f"""CREATE TABLE IF NOT EXISTS customers (
            id               TEXT PRIMARY KEY,
            email            TEXT NOT NULL,
            answers_json     TEXT NOT NULL DEFAULT '{{}}',
            folder_id        TEXT NOT NULL DEFAULT '',
            ordered_per_day  {ic} NOT NULL DEFAULT 10,
            status           TEXT NOT NULL DEFAULT 'onboarding',
            plan             TEXT NOT NULL DEFAULT '',
            stripe_customer_id TEXT NOT NULL DEFAULT '',
            client_folder_url TEXT NOT NULL DEFAULT '',
            apollo_api_key   TEXT NOT NULL DEFAULT '',
            access_expires_at {ts} NOT NULL DEFAULT 0,
            generating_since {ts} NOT NULL DEFAULT 0,
            created_at       {ts} NOT NULL,
            updated_at       {ts} NOT NULL
        )""",
        f"""CREATE TABLE IF NOT EXISTS promo_redemptions (
            code          TEXT PRIMARY KEY,
            customer_id   TEXT NOT NULL,
            redeemed_at   {ts} NOT NULL
        )""",
        f"""CREATE TABLE IF NOT EXISTS runs (
            id           TEXT PRIMARY KEY,
            customer_id  TEXT NOT NULL,
            run_date     TEXT NOT NULL,
            ordered      {ic} NOT NULL,
            delivered    {ic} NOT NULL,
            csv_path     TEXT,
            sheet_url    TEXT,
            status       TEXT NOT NULL DEFAULT 'ok',
            error        TEXT NOT NULL DEFAULT '',
            created_at   {ts} NOT NULL
        )""",
        f"""CREATE TABLE IF NOT EXISTS password_resets (
            token       TEXT PRIMARY KEY,
            user_id     TEXT NOT NULL,
            created_at  {ts} NOT NULL,
            used        {ic} NOT NULL DEFAULT 0
        )""",
        f"""CREATE TABLE IF NOT EXISTS sent_emails (
            customer_id   TEXT NOT NULL,
            email_key     TEXT NOT NULL,
            sent_at       {ts} NOT NULL,
            PRIMARY KEY (customer_id, email_key)
        )""",
        f"""CREATE TABLE IF NOT EXISTS delivered_prospects (
            customer_id   TEXT NOT NULL,
            dedupe_key    TEXT NOT NULL,
            email         TEXT,
            linkedin_url  TEXT,
            full_name     TEXT,
            company       TEXT,
            delivered_at  {ts} NOT NULL,
            PRIMARY KEY (customer_id, dedupe_key)
        )""",
        f"""CREATE TABLE IF NOT EXISTS promo_codes (
            code            TEXT PRIMARY KEY,
            label           TEXT NOT NULL DEFAULT '',
            ordered_per_day {ic} NOT NULL DEFAULT 10,
            days            {ic} NOT NULL DEFAULT 7,
            created_at      {ts} NOT NULL
        )""",
        # --- LeadDaily (CRM add-on) ------------------------------------
        # Full prospect record captured at delivery so "Add to CRM" carries
        # every field (the delivered_prospects ledger only holds dedupe keys).
        f"""CREATE TABLE IF NOT EXISTS report_prospects (
            id            TEXT PRIMARY KEY,
            customer_id   TEXT NOT NULL,
            run_date      TEXT NOT NULL DEFAULT '',
            full_name     TEXT NOT NULL DEFAULT '',
            title         TEXT NOT NULL DEFAULT '',
            company       TEXT NOT NULL DEFAULT '',
            email         TEXT NOT NULL DEFAULT '',
            data_json     TEXT NOT NULL DEFAULT '{{}}',
            added_lead_id TEXT NOT NULL DEFAULT '',
            created_at    {ts} NOT NULL
        )""",
        f"""CREATE TABLE IF NOT EXISTS leads (
            id               TEXT PRIMARY KEY,
            customer_id      TEXT NOT NULL,
            source_prospect_id TEXT NOT NULL DEFAULT '',
            full_name        TEXT NOT NULL DEFAULT '',
            title            TEXT NOT NULL DEFAULT '',
            company          TEXT NOT NULL DEFAULT '',
            email            TEXT NOT NULL DEFAULT '',
            phone            TEXT NOT NULL DEFAULT '',
            linkedin_url     TEXT NOT NULL DEFAULT '',
            website          TEXT NOT NULL DEFAULT '',
            industry         TEXT NOT NULL DEFAULT '',
            fit_reason       TEXT NOT NULL DEFAULT '',
            intro_subject    TEXT NOT NULL DEFAULT '',
            intro_body       TEXT NOT NULL DEFAULT '',
            linkedin_message TEXT NOT NULL DEFAULT '',
            stage            TEXT NOT NULL DEFAULT 'new',
            value            {ic} NOT NULL DEFAULT 0,
            created_at       {ts} NOT NULL,
            updated_at       {ts} NOT NULL
        )""",
        f"""CREATE TABLE IF NOT EXISTS lead_activities (
            id            TEXT PRIMARY KEY,
            customer_id   TEXT NOT NULL,
            lead_id       TEXT NOT NULL,
            kind          TEXT NOT NULL DEFAULT 'note',
            body          TEXT NOT NULL DEFAULT '',
            created_at    {ts} NOT NULL
        )""",
        f"""CREATE TABLE IF NOT EXISTS lead_tasks (
            id            TEXT PRIMARY KEY,
            customer_id   TEXT NOT NULL,
            lead_id       TEXT NOT NULL,
            title         TEXT NOT NULL DEFAULT '',
            due_at        {ts} NOT NULL DEFAULT 0,
            done          {ic} NOT NULL DEFAULT 0,
            created_at    {ts} NOT NULL
        )""",
        # --- Affiliate program ----------------------------------------
        f"""CREATE TABLE IF NOT EXISTS affiliates (
            id               TEXT PRIMARY KEY,
            user_id          TEXT NOT NULL,
            email            TEXT NOT NULL DEFAULT '',
            name             TEXT NOT NULL DEFAULT '',
            code             TEXT UNIQUE NOT NULL,
            commission_bps   {ic} NOT NULL DEFAULT 2000,
            stripe_connect_id TEXT NOT NULL DEFAULT '',
            connect_status   TEXT NOT NULL DEFAULT 'none',
            status           TEXT NOT NULL DEFAULT 'active',
            created_at       {ts} NOT NULL
        )""",
        f"""CREATE TABLE IF NOT EXISTS referral_clicks (
            id           TEXT PRIMARY KEY,
            code         TEXT NOT NULL,
            landing      TEXT NOT NULL DEFAULT '',
            created_at   {ts} NOT NULL
        )""",
        f"""CREATE TABLE IF NOT EXISTS referrals (
            id                 TEXT PRIMARY KEY,
            affiliate_id       TEXT NOT NULL,
            referred_user_id   TEXT UNIQUE NOT NULL,
            referred_email     TEXT NOT NULL DEFAULT '',
            stripe_customer_id TEXT NOT NULL DEFAULT '',
            status             TEXT NOT NULL DEFAULT 'signed_up',
            created_at         {ts} NOT NULL,
            converted_at       {ts} NOT NULL DEFAULT 0
        )""",
        f"""CREATE TABLE IF NOT EXISTS commissions (
            id                TEXT PRIMARY KEY,
            affiliate_id      TEXT NOT NULL,
            referral_id       TEXT NOT NULL DEFAULT '',
            stripe_invoice_id TEXT UNIQUE NOT NULL,
            gross_cents       {ic} NOT NULL DEFAULT 0,
            amount_cents      {ic} NOT NULL DEFAULT 0,
            currency          TEXT NOT NULL DEFAULT 'usd',
            status            TEXT NOT NULL DEFAULT 'pending',
            earned_at         {ts} NOT NULL,
            payable_at        {ts} NOT NULL DEFAULT 0,
            paid_at           {ts} NOT NULL DEFAULT 0,
            payout_id         TEXT NOT NULL DEFAULT '',
            created_at        {ts} NOT NULL
        )""",
        f"""CREATE TABLE IF NOT EXISTS affiliate_payouts (
            id                TEXT PRIMARY KEY,
            affiliate_id      TEXT NOT NULL,
            amount_cents      {ic} NOT NULL DEFAULT 0,
            currency          TEXT NOT NULL DEFAULT 'usd',
            stripe_transfer_id TEXT NOT NULL DEFAULT '',
            status            TEXT NOT NULL DEFAULT 'pending',
            created_at        {ts} NOT NULL
        )""",
        # Voice-matching: examples of a customer's own outreach (their edited
        # drafts) used as few-shot guidance so future drafts sound like them.
        f"""CREATE TABLE IF NOT EXISTS voice_examples (
            id            TEXT PRIMARY KEY,
            customer_id   TEXT NOT NULL,
            kind          TEXT NOT NULL DEFAULT 'email',
            text          TEXT NOT NULL DEFAULT '',
            created_at    {ts} NOT NULL
        )""",
        # AI support chat: conversations + their messages, kept so the operator
        # gets a daily digest and can see what people ask.
        f"""CREATE TABLE IF NOT EXISTS chat_conversations (
            id            TEXT PRIMARY KEY,
            session_id    TEXT NOT NULL DEFAULT '',
            context       TEXT NOT NULL DEFAULT 'site',
            customer_id   TEXT NOT NULL DEFAULT '',
            email         TEXT NOT NULL DEFAULT '',
            msg_count     {ic} NOT NULL DEFAULT 0,
            created_at    {ts} NOT NULL,
            last_at       {ts} NOT NULL DEFAULT 0
        )""",
        f"""CREATE TABLE IF NOT EXISTS chat_messages (
            id              TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            role            TEXT NOT NULL DEFAULT 'user',
            content         TEXT NOT NULL DEFAULT '',
            created_at      {ts} NOT NULL
        )""",
    ]
    # Best-effort migrations for columns added after a table already existed.
    migrations = [
        "ALTER TABLE customers ADD COLUMN client_folder_url TEXT NOT NULL DEFAULT ''",
        f"ALTER TABLE customers ADD COLUMN access_expires_at {ts} NOT NULL DEFAULT 0",
        f"ALTER TABLE customers ADD COLUMN generating_since {ts} NOT NULL DEFAULT 0",
        "ALTER TABLE customers ADD COLUMN apollo_api_key TEXT NOT NULL DEFAULT ''",
        f"ALTER TABLE customers ADD COLUMN crm_enabled {ic} NOT NULL DEFAULT 0",
        f"ALTER TABLE customers ADD COLUMN comp {ic} NOT NULL DEFAULT 0",
        "ALTER TABLE customers ADD COLUMN voice_sample TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE customers ADD COLUMN voice_notes TEXT NOT NULL DEFAULT ''",
        "CREATE INDEX IF NOT EXISTS idx_voice_examples_cust "
        "ON voice_examples (customer_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_report_prospects_cust "
        "ON report_prospects (customer_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_leads_cust ON leads (customer_id, stage)",
        "CREATE INDEX IF NOT EXISTS idx_lead_activities_lead "
        "ON lead_activities (lead_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_lead_tasks_cust "
        "ON lead_tasks (customer_id, done, due_at)",
        "CREATE INDEX IF NOT EXISTS idx_affiliates_user ON affiliates (user_id)",
        "CREATE INDEX IF NOT EXISTS idx_referrals_affiliate "
        "ON referrals (affiliate_id)",
        "CREATE INDEX IF NOT EXISTS idx_referrals_stripe_cust "
        "ON referrals (stripe_customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_commissions_affiliate "
        "ON commissions (affiliate_id, status)",
    ]
    conn = _connect_raw()
    try:
        # Create tables and COMMIT them first. On Postgres a later failed
        # statement aborts the whole transaction, so migrations must not share
        # it with the CREATE TABLEs.
        for stmt in ddl:
            conn.execute(stmt)
        conn.commit()
        # Each migration in its own transaction: a failure (e.g. the column
        # already exists) rolls back only itself, leaving the schema intact.
        for stmt in migrations:
            try:
                conn.execute(stmt)
                conn.commit()
            except Exception:
                conn.rollback()
    finally:
        conn.close()
