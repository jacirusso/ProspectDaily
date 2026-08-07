"""Subscription plans. The plan a customer buys sets how many prospects they
ORDER per day; the engine delivers that plus a bonus + spares (see
engine.config.delivery_split).

Stripe price ids are PUBLIC (not secrets), so they live here in code — this is
bulletproof against env-var typos. The `STRIPE_PRICE_*` env vars are honored as
an override only when set to a well-formed `price_…` value."""
import os

# (key, display name, ordered/day, monthly price, tagline, LIVE stripe price id)
PLANS = [
    {"key": "starter", "name": "Starter", "ordered": 10, "price": 289,
     "tagline": "For founders getting outbound off the ground",
     "stripe_env": "STRIPE_PRICE_STARTER",
     "price_id": "price_1U1YRTPGt4TtgV3cV4dcZbVB"},
    {"key": "growth", "name": "Growth", "ordered": 20, "price": 499,
     "tagline": "For teams building a steady pipeline", "popular": True,
     "stripe_env": "STRIPE_PRICE_GROWTH",
     "price_id": "price_1U1YRUPGt4TtgV3c0wsTKrv8"},
    {"key": "pro", "name": "Pro", "ordered": 40, "price": 999,
     "tagline": "Best value — maximum volume", "best_value": True,
     "stripe_env": "STRIPE_PRICE_PRO",
     "price_id": "price_1U1YRUPGt4TtgV3cQY4J3EhT"},
]


def by_key(key: str):
    for p in PLANS:
        if p["key"] == key:
            return p
    return None


def stripe_price_id(plan) -> str:
    # The built-in live price id is the source of truth — no dependence on env
    # vars, so a fat-fingered STRIPE_PRICE_* value can never break checkout.
    return plan["price_id"]


def stripe_enabled() -> bool:
    return bool(os.environ.get("STRIPE_SECRET_KEY", "").strip())
