"""Free / test promo codes.

Redeeming a code activates the account for free (no Stripe) at the code's daily
volume. Codes are SINGLE-USE (tracked in promo_redemptions) and may expire after
`days` (None = never). Add friends' codes here.
"""

# code (lowercase) -> {label, ordered_per_day, days}
PROMO_CODES = {
    "karafree": {"label": "Starter — 1 year (test)", "ordered_per_day": 10, "days": 365},
    "lizfree":  {"label": "Starter — 7 days (test)", "ordered_per_day": 10, "days": 7},
}


def lookup(code: str):
    return PROMO_CODES.get((code or "").strip().lower())
