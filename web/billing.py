"""Stripe billing: create checkout sessions and handle webhooks so a plan only
activates after a confirmed payment. All Stripe calls are lazy-imported and only
run when STRIPE_SECRET_KEY is set, so local dev works without Stripe installed.
"""
import logging
import os

from engine import config
from web import store, plans, affiliates

log = logging.getLogger("billing")


def _stripe():
    import stripe
    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
    return stripe


def create_checkout_url(user: dict, plan: dict, interval: str = "month") -> str:
    stripe = _stripe()
    base = os.environ.get("APP_BASE_URL", "http://localhost:8000")
    price_id = plans.stripe_price_id(plan, interval)
    if not price_id:
        raise RuntimeError(
            f"No Stripe price id configured for plan '{plan['key']}' "
            f"(set {plan['stripe_env']}).")
    cadence = (f"Billed annually at ${plan.get('annual'):,}/yr (2 months free)."
               if interval == "year" else "Billed monthly.")
    blurb = (
        f"You're subscribing to ProspectDaily {plan['name']} — "
        f"{plan['ordered']} verified B2B prospects delivered to your Google Drive "
        f"every weekday, each with a researched, ready-to-send intro email. "
        f"{cadence} Cancel anytime from your dashboard. "
        f"Note: ProspectDaily is operated by Brand State U, so \"Brand State U\" "
        f"may appear on your receipt and card statement.")
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=f"{base}/dashboard?welcome=1",
        cancel_url=f"{base}/plans",
        customer_email=user["email"],
        client_reference_id=user["id"],           # who to activate
        metadata={"plan_key": plan["key"], "user_id": user["id"]},
        custom_text={"submit": {"message": blurb}},
        subscription_data={
            "description": f"ProspectDaily {plan['name']} — daily B2B prospects",
            "metadata": {"user_id": user["id"], "plan_key": plan["key"]}},
    )
    return session.url


def handle_webhook(payload: bytes, sig_header: str) -> str:
    """Verify + process a Stripe event. Returns a short status string."""
    stripe = _stripe()
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    if secret:
        event = stripe.Webhook.construct_event(payload, sig_header, secret)
    else:                       # dev fallback: trust the parsed body (unsigned)
        import json
        event = json.loads(payload)
        log.warning("STRIPE_WEBHOOK_SECRET unset — webhook signature NOT verified")

    etype = event["type"]
    obj = event["data"]["object"]

    if etype == "checkout.session.completed":
        user_id = (obj.get("client_reference_id")
                   or (obj.get("metadata") or {}).get("user_id"))
        plan_key = (obj.get("metadata") or {}).get("plan_key")
        plan = plans.by_key(plan_key) if plan_key else None
        if user_id and plan:
            store.update_customer(
                user_id, plan=plan["key"], ordered_per_day=plan["ordered"],
                status="active", stripe_customer_id=obj.get("customer") or "")
            log.info("activated plan %s for user %s", plan["key"], user_id)
            # If this customer was referred, record the Stripe customer id on the
            # referral so renewal invoices can be matched back to the affiliate.
            try:
                if obj.get("customer"):
                    affiliates.link_referral_customer(user_id, obj["customer"])
            except Exception:
                log.exception("referral link on checkout failed")
            return "activated"

    elif etype in ("invoice.payment_succeeded", "invoice.paid"):
        _accrue_affiliate_commission(obj)
        return "commission-checked"

    elif etype == "charge.refunded":
        # Full refund inside the hold window reverses an unpaid commission.
        if obj.get("invoice") and obj.get("amount_refunded") == obj.get("amount"):
            n = affiliates.reverse_commission(obj["invoice"])
            if n:
                log.info("reversed %d commission(s) for refunded invoice %s",
                         n, obj["invoice"])
        return "refund-checked"

    elif etype == "account.updated":       # Stripe Connect onboarding progress
        status = "active" if obj.get("payouts_enabled") else "pending"
        affiliates.update_connect_status_by_account(obj.get("id") or "", status)
        return "connect-updated"

    elif etype in ("customer.subscription.deleted",
                   "customer.subscription.paused"):
        cust = store.get_customer_by_stripe_id(obj.get("customer") or "")
        if cust:
            store.update_customer(cust["id"], status="paused")
            log.info("paused customer %s (subscription ended)", cust["id"])
            return "paused"

    elif etype == "customer.subscription.resumed":
        cust = store.get_customer_by_stripe_id(obj.get("customer") or "")
        if cust and cust.get("plan"):
            store.update_customer(cust["id"], status="active")
            return "resumed"

    return "ignored"


def change_plan(customer: dict, plan: dict, interval: str = "month") -> None:
    """Modify the customer's active Stripe subscription to a new plan's price
    (Stripe prorates the difference automatically)."""
    stripe = _stripe()
    subs = stripe.Subscription.list(customer=customer["stripe_customer_id"],
                                    status="active", limit=1)
    data = subs.get("data", [])
    if not data:
        raise RuntimeError("No active subscription to modify.")
    sub = data[0]
    item_id = sub["items"]["data"][0]["id"]
    stripe.Subscription.modify(
        sub["id"],
        items=[{"id": item_id, "price": plans.stripe_price_id(plan, interval)}],
        proration_behavior="create_prorations")


def add_leaddaily(customer: dict) -> None:
    """Add the $49/mo LeadDaily add-on as a second item on the customer's active
    subscription (Stripe prorates). Idempotent — a no-op if it's already there.
    Raises if there's no configured price or no active subscription to attach to."""
    price_id = plans.leaddaily_price_id()
    if not price_id:
        raise RuntimeError("STRIPE_PRICE_LEADDAILY not set — create the $49/mo "
                           "LeadDaily price in Stripe and set the env var.")
    stripe = _stripe()
    subs = stripe.Subscription.list(customer=customer["stripe_customer_id"],
                                    status="active", limit=1)
    data = subs.get("data", [])
    if not data:
        raise RuntimeError("No active subscription to add LeadDaily to.")
    sub = data[0]
    for it in sub["items"]["data"]:                     # already attached?
        if it["price"]["id"] == price_id:
            return
    stripe.SubscriptionItem.create(
        subscription=sub["id"], price=price_id, quantity=1,
        proration_behavior="create_prorations")


def remove_leaddaily(customer: dict) -> None:
    """Remove the LeadDaily add-on item from the customer's subscription, if present."""
    price_id = plans.leaddaily_price_id()
    if not price_id or not customer.get("stripe_customer_id"):
        return
    stripe = _stripe()
    subs = stripe.Subscription.list(customer=customer["stripe_customer_id"],
                                    status="active", limit=1)
    for sub in subs.get("data", []):
        for it in sub["items"]["data"]:
            if it["price"]["id"] == price_id:
                stripe.SubscriptionItem.delete(
                    it["id"], proration_behavior="create_prorations")


def cancel_subscription_for(customer: dict) -> None:
    """Cancel the customer's Stripe subscription at period end, if any."""
    if not plans.stripe_enabled() or not customer.get("stripe_customer_id"):
        return
    stripe = _stripe()
    subs = stripe.Subscription.list(customer=customer["stripe_customer_id"],
                                    status="active", limit=1)
    for sub in subs.get("data", []):
        stripe.Subscription.modify(sub["id"], cancel_at_period_end=True)


# --- Affiliate program: commissions + Stripe Connect payouts -------------
def _accrue_affiliate_commission(obj) -> None:
    """From a paid invoice, credit the referring affiliate their share (deduped
    by invoice id, so first payment and every renewal each count once). Resolves
    the affiliate by Stripe customer id, with an email fallback in case the
    checkout webhook hasn't linked the customer to the referral yet."""
    try:
        invoice_id = obj.get("id")
        cid = obj.get("customer") or ""
        gross = int(obj.get("amount_paid") or 0)
        currency = obj.get("currency") or "usd"
        if not invoice_id or gross <= 0:
            return
        ref = affiliates.referral_by_stripe_customer(cid)
        if not ref:
            email = (obj.get("customer_email") or "").lower()
            user = store.get_user_by_email(email) if email else None
            if user:
                ref = affiliates.referral_by_user(user["id"])
                if ref and cid:
                    affiliates.link_referral_customer(user["id"], cid)
        if not ref:
            return
        if affiliates.record_commission(ref["affiliate_id"], ref["id"],
                                        invoice_id, gross, currency):
            affiliates.mark_referral_converted(ref["id"])
            log.info("commission accrued: affiliate=%s invoice=%s gross=%d",
                     ref["affiliate_id"], invoice_id, gross)
    except Exception:
        log.exception("affiliate commission accrual failed")


def create_connect_account(affiliate: dict) -> str:
    """Create a Stripe Connect Express account for an affiliate (transfers only)."""
    stripe = _stripe()
    acct = stripe.Account.create(
        type="express",
        email=affiliate.get("email") or None,
        capabilities={"transfers": {"requested": True}},
        business_type="individual",
        metadata={"affiliate_id": affiliate["id"],
                  "affiliate_code": affiliate["code"]})
    return acct["id"]


def connect_onboarding_url(connect_id: str, refresh_url: str,
                           return_url: str) -> str:
    stripe = _stripe()
    link = stripe.AccountLink.create(
        account=connect_id, refresh_url=refresh_url, return_url=return_url,
        type="account_onboarding")
    return link["url"]


def connect_status(connect_id: str) -> str:
    """Live payout-readiness for a connected account: 'active' once Stripe has
    everything it needs to pay them, else 'pending'."""
    stripe = _stripe()
    acct = stripe.Account.retrieve(connect_id)
    return "active" if acct.get("payouts_enabled") else "pending"


def run_affiliate_payouts() -> dict:
    """Automated sweep: transfer each Connect-ready affiliate's payable balance
    (>= the minimum) from the platform Stripe balance to their account. Called
    from the daily cron and the admin 'Run payouts' button. Per-affiliate errors
    are isolated so one failure can't stop the rest."""
    if not plans.stripe_enabled():
        return {"paid": 0, "note": "stripe disabled"}
    stripe = _stripe()
    ready = affiliates.affiliates_ready_for_payout(config.AFFILIATE_MIN_PAYOUT_CENTS)
    paid, total = 0, 0
    for item in ready:
        aff, amt, cids = item["affiliate"], item["amount_cents"], item["commission_ids"]
        try:
            tr = stripe.Transfer.create(
                amount=amt, currency="usd",
                destination=aff["stripe_connect_id"],
                metadata={"affiliate_id": aff["id"], "affiliate_code": aff["code"]})
            pid = affiliates.create_payout(aff["id"], amt, "usd", tr["id"], "paid")
            affiliates.mark_commissions_paid(cids, pid)
            paid += 1
            total += amt
            log.info("paid affiliate %s $%.2f (transfer %s)",
                     aff["code"], amt / 100, tr["id"])
        except Exception:
            log.exception("payout failed for affiliate %s", aff["id"])
            affiliates.create_payout(aff["id"], amt, "usd", "", "failed")
    return {"paid": paid, "total_cents": total, "candidates": len(ready)}
