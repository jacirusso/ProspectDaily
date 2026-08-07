"""The affiliate program's data + business layer.

Affiliates are ordinary user accounts that opted in (so they reuse login and get
their own dashboard). A referral link carries the affiliate's `code`; when a
referred visitor signs up and pays, the affiliate earns a recurring share of
every payment. Payouts run automatically over Stripe Connect.

Money model (all amounts in integer cents):
  - commission = payment_amount * commission_bps / 10000  (default 20%)
  - a commission is 'pending' until payable_at (earned + hold days), then payable
  - refunds inside the hold flip it to 'reversed' (no money went out)
  - the sweep pays affiliates whose payable balance >= the minimum threshold

Same db layer as the rest of the app: SQLite locally, Postgres in production.
"""
import re
import time
import uuid
from typing import Dict, List, Optional

from engine import db, config


def _now() -> int:
    return int(time.time())


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "", (text or "").lower())
    return s[:12]


def _gen_code(seed: str) -> str:
    """A short, unique, URL-safe referral code derived from a name/email seed."""
    base = _slug(seed) or "aff"
    for _ in range(20):
        code = f"{base}{uuid.uuid4().hex[:4]}"
        if not get_affiliate_by_code(code):
            return code
    return uuid.uuid4().hex[:12]


# --- Affiliate accounts -------------------------------------------------
def get_affiliate_by_user(user_id: str) -> Optional[Dict]:
    db.init_schema()
    return db.query_one("SELECT * FROM affiliates WHERE user_id = ?", (user_id,))


def get_affiliate_by_code(code: str) -> Optional[Dict]:
    if not code:
        return None
    db.init_schema()
    return db.query_one("SELECT * FROM affiliates WHERE code = ?",
                        ((code or "").strip().lower(),))


def get_affiliate(affiliate_id: str) -> Optional[Dict]:
    db.init_schema()
    return db.query_one("SELECT * FROM affiliates WHERE id = ?", (affiliate_id,))


def create_affiliate(user_id: str, email: str, name: str = "") -> Dict:
    """Turn a user into an affiliate. Idempotent — returns the existing affiliate
    if they already joined."""
    existing = get_affiliate_by_user(user_id)
    if existing:
        return existing
    aff_id = uuid.uuid4().hex
    code = _gen_code(name or (email.split("@")[0] if email else ""))
    db.execute(
        "INSERT INTO affiliates (id, user_id, email, name, code, commission_bps, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (aff_id, user_id, (email or "").lower(), name or "", code,
         config.AFFILIATE_COMMISSION_BPS, _now()))
    return get_affiliate(aff_id)


def set_connect_account(affiliate_id: str, connect_id: str, status: str) -> None:
    db.execute("UPDATE affiliates SET stripe_connect_id = ?, connect_status = ? "
               "WHERE id = ?", (connect_id, status, affiliate_id))


def update_connect_status_by_account(connect_id: str, status: str) -> None:
    db.execute("UPDATE affiliates SET connect_status = ? WHERE stripe_connect_id = ?",
               (status, connect_id))


def affiliate_by_connect_id(connect_id: str) -> Optional[Dict]:
    if not connect_id:
        return None
    db.init_schema()
    return db.query_one("SELECT * FROM affiliates WHERE stripe_connect_id = ?",
                        (connect_id,))


# --- Clicks + referral attribution --------------------------------------
def record_click(code: str, landing: str = "") -> None:
    if not get_affiliate_by_code(code):
        return
    db.execute("INSERT INTO referral_clicks (id, code, landing, created_at) "
               "VALUES (?, ?, ?, ?)",
               (uuid.uuid4().hex, (code or "").strip().lower(), landing[:200], _now()))


def attribute_signup(code: str, referred_user_id: str,
                     referred_email: str) -> Optional[str]:
    """Tie a brand-new signup to the affiliate whose code they arrived with.
    Skips self-referrals and anyone already referred. Returns the referral id."""
    aff = get_affiliate_by_code(code)
    if not aff or aff.get("status") != "active":
        return None
    if (referred_email or "").lower() == (aff.get("email") or "").lower():
        return None                              # no self-referrals
    if referral_by_user(referred_user_id):
        return None                              # already attributed
    rid = uuid.uuid4().hex
    n = db.execute(
        "INSERT INTO referrals (id, affiliate_id, referred_user_id, referred_email, "
        "created_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
        (rid, aff["id"], referred_user_id, (referred_email or "").lower(), _now()))
    return rid if n > 0 else None


def referral_by_user(referred_user_id: str) -> Optional[Dict]:
    db.init_schema()
    return db.query_one("SELECT * FROM referrals WHERE referred_user_id = ?",
                        (referred_user_id,))


def referral_by_stripe_customer(stripe_customer_id: str) -> Optional[Dict]:
    if not stripe_customer_id:
        return None
    db.init_schema()
    return db.query_one("SELECT * FROM referrals WHERE stripe_customer_id = ?",
                        (stripe_customer_id,))


def link_referral_customer(referred_user_id: str, stripe_customer_id: str) -> None:
    """Record the Stripe customer id on the referral (set at first checkout) and
    mark it converted, so later renewal invoices can be matched to the affiliate."""
    ref = referral_by_user(referred_user_id)
    if not ref:
        return
    converted = ref["converted_at"] or _now()
    status = "converted" if ref["status"] == "signed_up" else ref["status"]
    db.execute("UPDATE referrals SET stripe_customer_id = ?, status = ?, "
               "converted_at = ? WHERE referred_user_id = ?",
               (stripe_customer_id, status, converted, referred_user_id))


def mark_referral_converted(referral_id: str) -> None:
    db.execute("UPDATE referrals SET status = 'converted', converted_at = ? "
               "WHERE id = ? AND status = 'signed_up'", (_now(), referral_id))


# --- Commissions --------------------------------------------------------
def record_commission(affiliate_id: str, referral_id: str, stripe_invoice_id: str,
                      gross_cents: int, currency: str = "usd") -> bool:
    """Accrue one commission for a paid invoice. Deduped on the invoice id, so a
    re-delivered webhook can never double-credit. Returns True if newly created."""
    if not stripe_invoice_id or gross_cents <= 0:
        return False
    aff = get_affiliate(affiliate_id)
    bps = int(aff["commission_bps"]) if aff else config.AFFILIATE_COMMISSION_BPS
    amount = (gross_cents * bps) // 10000
    if amount <= 0:
        return False
    now = _now()
    payable_at = now + config.AFFILIATE_HOLD_DAYS * 86400
    n = db.execute(
        "INSERT INTO commissions (id, affiliate_id, referral_id, stripe_invoice_id, "
        "gross_cents, amount_cents, currency, status, earned_at, payable_at, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?) "
        "ON CONFLICT DO NOTHING",
        (uuid.uuid4().hex, affiliate_id, referral_id or "", stripe_invoice_id,
         gross_cents, amount, currency, now, payable_at, now))
    return n > 0


def reverse_commission(stripe_invoice_id: str) -> int:
    """Reverse an unpaid commission for a refunded invoice. Only affects pending
    commissions — money already paid out is not clawed back automatically."""
    if not stripe_invoice_id:
        return 0
    return db.execute(
        "UPDATE commissions SET status = 'reversed' "
        "WHERE stripe_invoice_id = ? AND status = 'pending'", (stripe_invoice_id,))


def balances(affiliate_id: str) -> Dict[str, int]:
    """Money summary for an affiliate, in cents."""
    db.init_schema()
    now = _now()
    rows = db.query("SELECT status, amount_cents, payable_at FROM commissions "
                    "WHERE affiliate_id = ?", (affiliate_id,))
    pending = payable = paid = reversed_ = lifetime = 0
    for r in rows:
        amt = int(r["amount_cents"])
        st = r["status"]
        if st == "paid":
            paid += amt
            lifetime += amt
        elif st == "reversed":
            reversed_ += amt
        elif st == "pending":
            lifetime += amt
            if int(r["payable_at"]) <= now:
                payable += amt
            else:
                pending += amt
    return {"pending": pending, "payable": payable, "paid": paid,
            "reversed": reversed_, "lifetime": lifetime}


def stats(affiliate_id: str) -> Dict[str, int]:
    db.init_schema()
    aff = get_affiliate(affiliate_id)
    clicks = db.query_one("SELECT COUNT(*) AS n FROM referral_clicks WHERE code = ?",
                          (aff["code"],)) if aff else None
    refs = db.query_one("SELECT COUNT(*) AS n FROM referrals WHERE affiliate_id = ?",
                        (affiliate_id,))
    conv = db.query_one("SELECT COUNT(*) AS n FROM referrals WHERE affiliate_id = ? "
                        "AND status = 'converted'", (affiliate_id,))
    return {"clicks": int(clicks["n"]) if clicks else 0,
            "referrals": int(refs["n"]) if refs else 0,
            "conversions": int(conv["n"]) if conv else 0}


def list_referrals(affiliate_id: str, limit: int = 100) -> List[Dict]:
    db.init_schema()
    return db.query("SELECT * FROM referrals WHERE affiliate_id = ? "
                    "ORDER BY created_at DESC LIMIT ?", (affiliate_id, limit))


def list_commissions(affiliate_id: str, limit: int = 100) -> List[Dict]:
    db.init_schema()
    return db.query("SELECT * FROM commissions WHERE affiliate_id = ? "
                    "ORDER BY created_at DESC LIMIT ?", (affiliate_id, limit))


def list_payouts(affiliate_id: str, limit: int = 100) -> List[Dict]:
    db.init_schema()
    return db.query("SELECT * FROM affiliate_payouts WHERE affiliate_id = ? "
                    "ORDER BY created_at DESC LIMIT ?", (affiliate_id, limit))


# --- Payout sweep support ----------------------------------------------
def payable_commission_ids(affiliate_id: str) -> List[str]:
    """Ids of this affiliate's commissions that are past their hold and unpaid."""
    db.init_schema()
    rows = db.query("SELECT id FROM commissions WHERE affiliate_id = ? "
                    "AND status = 'pending' AND payable_at <= ?",
                    (affiliate_id, _now()))
    return [r["id"] for r in rows]


def affiliates_ready_for_payout(min_cents: int) -> List[Dict]:
    """Every Connect-ready affiliate whose payable balance clears the threshold,
    with the exact commission ids to settle. Drives the automated sweep."""
    db.init_schema()
    ready = []
    rows = db.query("SELECT * FROM affiliates WHERE connect_status = 'active' "
                    "AND status = 'active'")
    for aff in rows:
        bal = balances(aff["id"])
        if bal["payable"] >= min_cents:
            ready.append({"affiliate": aff, "amount_cents": bal["payable"],
                          "commission_ids": payable_commission_ids(aff["id"])})
    return ready


def create_payout(affiliate_id: str, amount_cents: int, currency: str,
                  transfer_id: str, status: str) -> str:
    pid = uuid.uuid4().hex
    db.execute("INSERT INTO affiliate_payouts (id, affiliate_id, amount_cents, "
               "currency, stripe_transfer_id, status, created_at) "
               "VALUES (?, ?, ?, ?, ?, ?, ?)",
               (pid, affiliate_id, amount_cents, currency, transfer_id, status,
                _now()))
    return pid


def mark_commissions_paid(commission_ids: List[str], payout_id: str) -> None:
    if not commission_ids:
        return
    now = _now()
    for cid in commission_ids:
        db.execute("UPDATE commissions SET status = 'paid', paid_at = ?, "
                   "payout_id = ? WHERE id = ? AND status = 'pending'",
                   (now, payout_id, cid))


# --- Admin --------------------------------------------------------------
def all_affiliates() -> List[Dict]:
    db.init_schema()
    rows = db.query("SELECT * FROM affiliates ORDER BY created_at DESC")
    for a in rows:
        a.update(stats(a["id"]))
        a["balances"] = balances(a["id"])
    return rows


def program_totals() -> Dict[str, int]:
    db.init_schema()
    affs = db.query_one("SELECT COUNT(*) AS n FROM affiliates")
    refs = db.query_one("SELECT COUNT(*) AS n FROM referrals")
    conv = db.query_one("SELECT COUNT(*) AS n FROM referrals WHERE status='converted'")
    owed = db.query_one("SELECT COALESCE(SUM(amount_cents),0) AS c FROM commissions "
                        "WHERE status='pending'")
    paid = db.query_one("SELECT COALESCE(SUM(amount_cents),0) AS c FROM commissions "
                        "WHERE status='paid'")
    return {"affiliates": int(affs["n"]) if affs else 0,
            "referrals": int(refs["n"]) if refs else 0,
            "conversions": int(conv["n"]) if conv else 0,
            "owed_cents": int(owed["c"]) if owed else 0,
            "paid_cents": int(paid["c"]) if paid else 0}
