"""Subscription plans. The plan a customer buys sets how many prospects they
ORDER per day; the engine delivers that plus a bonus + spares (see
engine.config.delivery_split).

Stripe price ids are PUBLIC (not secrets), so they live here in code — this is
bulletproof against env-var typos. The `STRIPE_PRICE_*` env vars are honored as
an override only when set to a well-formed `price_…` value."""
import os

# (key, display name, ordered/day, monthly price, tagline, LIVE stripe price id)
# price = monthly USD; annual = yearly USD (2 months free ≈ 17% off). Both price
# ids are live and hardcoded (public, not secret) so checkout can't break on a
# fat-fingered env value.
PLANS = [
    {"key": "starter", "name": "Starter", "ordered": 10, "price": 289,
     "tagline": "For founders getting outbound off the ground",
     "stripe_env": "STRIPE_PRICE_STARTER",
     "price_id": "price_1U1YRTPGt4TtgV3cV4dcZbVB",
     "annual": 2890, "annual_price_id": "price_1U1qvdPGt4TtgV3cqWP7EzUo"},
    {"key": "growth", "name": "Growth", "ordered": 20, "price": 499,
     "tagline": "For teams building a steady pipeline", "popular": True,
     "stripe_env": "STRIPE_PRICE_GROWTH",
     "price_id": "price_1U1YRUPGt4TtgV3c0wsTKrv8",
     "annual": 4990, "annual_price_id": "price_1U1qvePGt4TtgV3c41c2Ij7m"},
    {"key": "pro", "name": "Pro", "ordered": 40, "price": 999,
     "tagline": "Best value — maximum volume", "best_value": True,
     "stripe_env": "STRIPE_PRICE_PRO",
     "price_id": "price_1U1YRUPGt4TtgV3cQY4J3EhT",
     "annual": 9990, "annual_price_id": "price_1U1qvfPGt4TtgV3cIaD1OGwx"},
]


# LeadDaily — the CRM add-on. A flat $49/mo item added onto any plan's Stripe
# subscription. The price id is NOT hardcoded because the product/price hasn't
# been created in Stripe yet — set STRIPE_PRICE_LEADDAILY once it exists.
LEADDAILY = {
    "key": "leaddaily",
    "name": "LeadDaily",
    "price": 49,
    "tagline": "Work your daily prospects into closed clients",
    "stripe_env": "STRIPE_PRICE_LEADDAILY",
}


def leaddaily_price_id() -> str:
    return os.environ.get("STRIPE_PRICE_LEADDAILY", "").strip()


def by_key(key: str):
    for p in PLANS:
        if p["key"] == key:
            return p
    return None


def stripe_price_id(plan, interval: str = "month") -> str:
    # Built-in live price ids are the source of truth — no dependence on env
    # vars, so a fat-fingered value can never break checkout.
    if interval == "year":
        return plan.get("annual_price_id", "") or plan["price_id"]
    return plan["price_id"]


def stripe_enabled() -> bool:
    return bool(os.environ.get("STRIPE_SECRET_KEY", "").strip())
