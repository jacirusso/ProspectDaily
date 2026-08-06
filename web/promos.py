"""Free / test promo codes.

Redeeming a code activates the account for free (no Stripe) at the code's daily
volume. Codes are SINGLE-USE (tracked in promo_redemptions) and may expire after
`days` (None = never). Add friends' codes here.
"""

# code (lowercase) -> {label, ordered_per_day, days}. Every code is single-use.
_STARTER_7 = {"label": "Starter — 7 days (test)", "ordered_per_day": 10, "days": 7}
PROMO_CODES = {
    "karafree":    {"label": "Starter — 1 year (test)", "ordered_per_day": 10, "days": 365},
    "jacibsu":     {"label": "Starter — 1 year (test)", "ordered_per_day": 10, "days": 365},
    "jacibsu1":    {"label": "Starter — 1 year (test)", "ordered_per_day": 10, "days": 365},
    "lizfree":     dict(_STARTER_7),
    "michaelfree": dict(_STARTER_7),
    "mollyfree":   dict(_STARTER_7),
    "morganfree":  dict(_STARTER_7),
    "jacksonfree": dict(_STARTER_7),
    "amberfree":   {"label": "Starter — 30 days (test)", "ordered_per_day": 10, "days": 30},
}


def lookup(code: str):
    return PROMO_CODES.get((code or "").strip().lower())
