# Prospect SaaS

A B2B prospecting service. Customers answer an onboarding questionnaire that
defines their target audience; every day the system finds fresh prospects,
writes a "why they're a fit" note and a personalized intro email for each, and
drops a report into a shared Google Drive folder.

**Core rules**
- Prospects are ordered in groups of 10.
- We always deliver **1.5× the ordered amount** (order 10 → get 15).
- **No prospect is ever delivered twice** to the same customer — enforced by a
  permanent dedupe ledger keyed on provider id + email + LinkedIn + name/company.

## Status

| Piece | Status |
|---|---|
| Prospecting engine (questionnaire → find → dedupe → report → deliver) | ✅ built & tested |
| Apollo.io provider | ✅ built (needs API key to go live) |
| Mock provider (for testing without keys) | ✅ built |
| AI copywriting (Claude + template fallback) | ✅ built |
| Google Drive delivery (service account) | ✅ built (needs credentials) |
| Web app (login, questionnaire UI, dashboard) | ✅ built & tested |
| Stripe billing (dev-mode activation + real checkout path) | ✅ built |
| Daily scheduler (`python -m engine.daily`) | ✅ built |
| Render deploy blueprint (`render.yaml`) | ✅ written |
| **Postgres storage for production** | ⚠️ **remaining** — see caveat below |

## ⚠️ One production caveat (read before deploying)

The app currently stores everything in **SQLite** (`data/prospects.db`). That is
perfect locally and it's fully tested. But the Render blueprint runs the web app
and the daily cron as **two separate services with separate filesystems**, so
they can't share a SQLite file — and Render web dynos have ephemeral disks.

**Before going live, pick one:**
- **Simplest:** run web + an in-process daily scheduler as a *single* Render
  service with a **persistent disk** for `data/`. No Postgres needed. (~1 small
  change: a scheduler thread instead of the cron service.)
- **Scales better:** wire the storage layer (`web/store.py` + `engine/dedupe.py`)
  to **Postgres** via `DATABASE_URL`. The table schemas are already
  Postgres-compatible; it's a mechanical swap of the `sqlite3` calls for `psycopg`.

This is the one clearly-labeled loose end — everything else runs today.

## Web app

```bash
./.venv/bin/uvicorn web.app:app --reload --port 8000   # then open localhost:8000
```

Flow: sign up → answer the questionnaire → pick a plan (activates instantly in
dev mode without Stripe) → "Generate today's report now" → download the CSV /
open the Google Sheet. The daily cron (`python -m engine.daily`) does this
automatically for every active customer each morning.

## Run the engine right now (no keys needed)

```bash
python3 -m engine.cli --demo
```

This generates 20 mock prospects (for an order of 10), writes a CSV to
`data/reports/demo/`, and prints samples. Run it again — you'll get 20 brand-new
prospects, never repeating earlier ones.

## Go live

1. Copy `.env.example` to `.env` and set `DATA_PROVIDER=apollo` + `APOLLO_API_KEY`.
2. Add `ANTHROPIC_API_KEY` for Claude-written emails (optional).
3. Add `GOOGLE_SERVICE_ACCOUNT_JSON` and share a Drive folder with the service
   account's email to enable delivery.
4. `pip install -r requirements.txt` (only needed for real Google/web features).

## Layout

```
engine/
  questionnaire.py   # the onboarding questions (drives the web form too)
  segments.py        # answers -> provider-neutral SearchSpec -> Apollo params
  providers/
    base.py          # Prospect model + identity/dedupe logic
    apollo.py        # real Apollo.io client (stdlib urllib)
    mock.py          # deterministic fake data for testing
  dedupe.py          # the permanent "never send twice" ledger
  ai.py              # Claude copywriting (+ template fallback)
  report.py          # the report columns / CSV writer
  google_drive.py    # service-account delivery to a Sheet
  pipeline.py        # orchestrator (the 1.5x + dedupe rules live here)
  cli.py             # run a customer from the command line
```
