"""Central configuration. Everything is read from environment variables so the
same code runs locally (stdlib-only, mock data) and in production (real APIs).

A local `.env` file (gitignored) is loaded automatically if present, so secrets
like the Apollo key live on disk, never in code or version control."""
import os


def _load_dotenv() -> None:
    """Minimal .env loader (no dependency). Real environment vars win over .env."""
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)


_load_dotenv()


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


# --- Data provider -------------------------------------------------------
# "mock"   = generate realistic fake prospects (no API key needed, for testing).
# "pdl"    = People Data Labs Person Search (redistribution-licensed; ONE
#            operator PDL_API_KEY serves all customers). The intended provider.
# "apollo" = Apollo.io API. NOTE: Apollo's terms forbid reselling their data in
#            a product, so this is for operator testing only, not production.
DATA_PROVIDER = _get("DATA_PROVIDER", "mock")

# People Data Labs (operator-level; we hold the license, customers bring nothing).
PDL_API_KEY = _get("PDL_API_KEY")
PDL_BASE_URL = _get("PDL_BASE_URL", "https://api.peopledatalabs.com/v5")

# --- PDF render service (separate container; see pdfservice/) -------------
# When set, daily reports are delivered as a polished PDF. Unset/unreachable =>
# graceful fallback to the Google Doc format.
PDF_SERVICE_URL = _get("PDF_SERVICE_URL")
PDF_SERVICE_SECRET = _get("PDF_SERVICE_SECRET")

APOLLO_API_KEY = _get("APOLLO_API_KEY")
APOLLO_BASE_URL = _get("APOLLO_BASE_URL", "https://api.apollo.io/api/v1")
# Enrich each kept prospect to reveal a VERIFIED EMAIL (~1 credit each). This is
# the core of the product, so it defaults on for real Apollo use.
APOLLO_REVEAL_EMAIL = _get("APOLLO_REVEAL_EMAIL", "true").lower() == "true"
# Phones are delivered ASYNCHRONOUSLY by Apollo to a webhook and draw from a
# separate, smaller mobile-credit bucket. Off by default; needs APOLLO_PHONE_WEBHOOK_URL.
APOLLO_REVEAL_PHONE = _get("APOLLO_REVEAL_PHONE", "false").lower() == "true"
APOLLO_PHONE_WEBHOOK_URL = _get("APOLLO_PHONE_WEBHOOK_URL")

# --- AI copywriting (fit rationale + intro email) ------------------------
ANTHROPIC_API_KEY = _get("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = _get("ANTHROPIC_MODEL", "claude-sonnet-5")

# --- Google Drive delivery ----------------------------------------------
# Two supported backends:
#  (a) OAuth "Connect Google" — for personal Gmail accounts. The user authorizes
#      once; files are created in THEIR Drive, owned by them. Preferred here.
#  (b) Service account — only works with a Workspace Shared Drive.
# OAuth takes precedence when a token file is present.
GOOGLE_OAUTH_CLIENT = _get("GOOGLE_OAUTH_CLIENT",
                           os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                        "oauth_client.json"))
GOOGLE_OAUTH_TOKEN = _get("GOOGLE_OAUTH_TOKEN",
                          os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                       "google_token.json"))
# Name of the folder the app creates/uses in the user's Drive for reports.
GOOGLE_DRIVE_FOLDER_NAME = _get("GOOGLE_DRIVE_FOLDER_NAME", "ProspectDaily Reports")
# Daily report format: "pdf" (polished PDF via WeasyPrint, default),
# "doc" (styled Google Doc), or "sheet". PDF falls back to Doc if WeasyPrint
# isn't available, so it's always safe.
REPORT_FORMAT = _get("REPORT_FORMAT", "pdf").lower()
# Service-account fallback (Workspace Shared Drives only).
GOOGLE_SERVICE_ACCOUNT_JSON = _get("GOOGLE_SERVICE_ACCOUNT_JSON")


def _materialize(env_var: str, path: str) -> None:
    """On hosts with an ephemeral filesystem (e.g. Render), let secret JSON
    files be provided as env vars: write them to the expected path if the file
    isn't already present locally."""
    val = os.environ.get(env_var, "").strip()
    if val and not os.path.exists(path):
        try:
            with open(path, "w") as f:
                f.write(val)
            os.chmod(path, 0o600)
        except OSError:
            pass


# In production, set these env vars to the JSON contents of the local files.
_materialize("GOOGLE_OAUTH_CLIENT_JSON", GOOGLE_OAUTH_CLIENT)
_materialize("GOOGLE_OAUTH_TOKEN_JSON", GOOGLE_OAUTH_TOKEN)

# --- Email delivery ------------------------------------------------------
# "resend" uses the Resend API (needs RESEND_API_KEY). Anything else / unset
# falls back to writing the email to data/outbox/ so the pipeline still runs.
EMAIL_PROVIDER = _get("EMAIL_PROVIDER", "resend" if _get("RESEND_API_KEY") else "file")
RESEND_API_KEY = _get("RESEND_API_KEY")
# Who reports come from. Until you verify a domain in Resend, use their test
# sender "onboarding@resend.dev" (can only send to your own account email).
# Domain prospectdaily.com is verified in Resend, so we send from it by default.
# Replies route to the operator's real inbox (no hello@ mailbox needed).
EMAIL_FROM = _get("EMAIL_FROM", "ProspectDaily <hello@prospectdaily.com>")
EMAIL_REPLY_TO = _get("EMAIL_REPLY_TO", "jaci@brandstateu.com")

# --- Storage (the dedupe ledger + app data) ------------------------------
# Local dev uses SQLite. Production sets DATABASE_URL to Postgres.
DATABASE_URL = _get("DATABASE_URL")
SQLITE_PATH = _get("SQLITE_PATH", os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "prospects.db"))

# --- Product rules -------------------------------------------------------
GROUP_SIZE = 10             # prospects are ordered in groups of 10

# Delivery model: customers get exactly the number of verified prospects they
# pay for — no over-delivery. With quality, verified data there's no need for
# spare swap-ins. (Both knobs default to 0; raise them if we ever want to add a
# bonus/spare block back — the report + pipeline still support tiers.)
BONUS_RATIO = float(_get("BONUS_RATIO", "0"))        # bonus = +% of the order
REPLACEMENT_COUNT = int(_get("REPLACEMENT_COUNT", "0"))  # spare swap-ins/report


def bonus_for(ordered: int) -> int:
    return int(round(ordered * BONUS_RATIO))


def delivered_for(ordered: int) -> int:
    """Total prospects actually placed in a report: core + bonus + spares."""
    return ordered + bonus_for(ordered) + REPLACEMENT_COUNT


def delivery_split(ordered: int) -> dict:
    """How a report's prospects break into sections."""
    return {"core": ordered, "bonus": bonus_for(ordered),
            "spares": REPLACEMENT_COUNT,
            "total": ordered + bonus_for(ordered) + REPLACEMENT_COUNT}


# Max prospects from the SAME company in one daily batch (1 = spread across
# distinct companies). Enforced pre-enrichment so we don't waste credits.
MAX_PER_COMPANY = int(_get("MAX_PER_COMPANY", "1") or "1")

# --- Operations / alerts -------------------------------------------------
# Where operational alerts (failed runs, low Apollo credits) are emailed.
OPERATOR_EMAIL = _get("OPERATOR_EMAIL", "jaci@brandstateu.com")

# --- Feature flags -------------------------------------------------------
# LeadDaily (the CRM add-on) is operator-only until launch: hidden from and
# inaccessible to customers. Flip this to "true" (env var, no redeploy needed)
# to open it to everyone.
LEADDAILY_PUBLIC = _get("LEADDAILY_PUBLIC", "").strip().lower() in (
    "1", "true", "yes", "on")
# Rough monthly Apollo credit budget (30k/yr ≈ 2500/mo). We estimate usage as
# "prospects delivered this month" (~1 credit each) and alert past ALERT_PCT.
APOLLO_MONTHLY_CREDITS = int(_get("APOLLO_MONTHLY_CREDITS", "2500"))
APOLLO_CREDIT_ALERT_PCT = float(_get("APOLLO_CREDIT_ALERT_PCT", "0.8"))
# Admin cost tracker estimates (all in USD/month).
MONTHLY_FIXED_COST = float(_get("MONTHLY_FIXED_COST", "70"))        # Render+domain
CLAUDE_COST_PER_10_PER_MONTH = float(_get("CLAUDE_COST_PER_10_PER_MONTH", "3"))
# Wholesale data cost per DELIVERED prospect record. PDL (call 2026-08-06):
# 20-24c/record on annual, email+phone included in the base credit. Using the
# annual list midpoint; first year is ~30% less with Amanda's promo code.
DATA_COST_PER_RECORD = float(_get("DATA_COST_PER_RECORD", "0.22"))
STRIPE_PCT = 0.029
STRIPE_FLAT = 0.30

# Delivery runs on WEEKDAYS ONLY (the cron is Mon–Fri). ~22 business days/month
# is used for the "per month" figures shown to customers.
WEEKDAYS_PER_MONTH = 22
