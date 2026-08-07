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
    "tommyfree7":  dict(_STARTER_7),
    "karafree7":   dict(_STARTER_7),
    "lizfree7":    dict(_STARTER_7),
    "melissafree7": dict(_STARTER_7),
}


def lookup(code: str):
    """Resolve a promo code -> its plan dict, or None. Checks the built-in codes
    first, then codes added at runtime via the admin dashboard (DB-backed)."""
    code_l = (code or "").strip().lower()
    built_in = PROMO_CODES.get(code_l)
    if built_in:
        return built_in
    try:
        from web import store
        return store.get_promo_code(code_l)
    except Exception:
        return None
