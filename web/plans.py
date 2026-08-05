"""Subscription plans. The plan a customer buys sets how many prospects they
ORDER per day; the engine always delivers 1.5x that. Stripe price ids come from
env vars so you can wire real prices without code changes."""
import os

# (key, display name, ordered/day, monthly price, tagline, stripe price env var)
PLANS = [
    {"key": "starter", "name": "Starter", "ordered": 10, "price": 189,
     "tagline": "For founders getting outbound off the ground",
     "stripe_env": "STRIPE_PRICE_STARTER"},
    {"key": "growth", "name": "Growth", "ordered": 20, "price": 297,
     "tagline": "For teams building a steady pipeline", "popular": True,
     "stripe_env": "STRIPE_PRICE_GROWTH"},
    {"key": "pro", "name": "Pro", "ordered": 40, "price": 398,
     "tagline": "Best value — maximum volume", "best_value": True,
     "stripe_env": "STRIPE_PRICE_PRO"},
]


def by_key(key: str):
    for p in PLANS:
        if p["key"] == key:
            return p
    return None


def stripe_price_id(plan) -> str:
    return os.environ.get(plan["stripe_env"], "").strip()


def stripe_enabled() -> bool:
    return bool(os.environ.get("STRIPE_SECRET_KEY", "").strip())
