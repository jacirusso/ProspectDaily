"""Stripe billing: create checkout sessions and handle webhooks so a plan only
activates after a confirmed payment. All Stripe calls are lazy-imported and only
run when STRIPE_SECRET_KEY is set, so local dev works without Stripe installed.
"""
import logging
import os

from web import store, plans

log = logging.getLogger("billing")


def _stripe():
    import stripe
    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
    return stripe


def create_checkout_url(user: dict, plan: dict) -> str:
    stripe = _stripe()
    base = os.environ.get("APP_BASE_URL", "http://localhost:8000")
    price_id = plans.stripe_price_id(plan)
    if not price_id:
        raise RuntimeError(
            f"No Stripe price id configured for plan '{plan['key']}' "
            f"(set {plan['stripe_env']}).")
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=f"{base}/dashboard?welcome=1",
        cancel_url=f"{base}/plans",
        customer_email=user["email"],
        client_reference_id=user["id"],           # who to activate
        metadata={"plan_key": plan["key"], "user_id": user["id"]},
        subscription_data={"metadata": {"user_id": user["id"],
                                        "plan_key": plan["key"]}},
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
            return "activated"

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


def change_plan(customer: dict, plan: dict) -> None:
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
        sub["id"], items=[{"id": item_id, "price": plans.stripe_price_id(plan)}],
        proration_behavior="create_prorations")


def cancel_subscription_for(customer: dict) -> None:
    """Cancel the customer's Stripe subscription at period end, if any."""
    if not plans.stripe_enabled() or not customer.get("stripe_customer_id"):
        return
    stripe = _stripe()
    subs = stripe.Subscription.list(customer=customer["stripe_customer_id"],
                                    status="active", limit=1)
    for sub in subs.get("data", []):
        stripe.Subscription.modify(sub["id"], cancel_at_period_end=True)
