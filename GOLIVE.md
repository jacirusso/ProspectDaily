# Go‑Live Checklist — ProspectDaily

From working-locally to paying-customers. **[Me]** = Claude does it in code.
**[You]** = needs your account / card / clicks (Claude will guide each one).

---

## Phase 1 — Make the code production-ready  **[Me]**
Nothing for you here; I do these next when you say go.

- [ ] 1.1 Switch storage from SQLite to **Postgres** (so the web app + daily job
      share one database). Schemas are already Postgres-ready.
- [ ] 1.2 Add the **Stripe webhook** so a subscription only activates after real
      payment (today it activates on redirect — fine for testing, not billing).
- [ ] 1.3 Add **email verification** on signup + a password-reset flow.
- [ ] 1.4 Add basic **error logging** so failed daily runs alert you.
- [ ] 1.5 Add a customer **"pause / cancel"** control on the dashboard.

## Phase 2 — Open the accounts & get keys  **[You]** (~30–45 min)
Create these and paste me the keys (or add them in Render later — I'll show you).

- [ ] 2.1 **Apollo.io** — sign up, pick a paid plan with API access, copy the
      **API key** (Settings → Integrations → API). ~$49–99/mo. *This is the one
      that makes the leads real.*
- [ ] 2.2 **Anthropic** — console.anthropic.com → API keys → create key. Pay-as-you-go
      (a few cents per prospect for the fit note + email). Optional; there's a
      template fallback without it.
- [ ] 2.3 **Stripe** — create an account, finish business verification so you can
      accept live payments.
- [ ] 2.4 **GitHub** — free account (to hold the code so Render can deploy it).
- [ ] 2.5 **Render** — free account (the host). Billing added at deploy.
- [ ] 2.6 **Domain** (optional but recommended) — buy e.g. prospectdaily.com.

## Phase 3 — Google Drive delivery  **[You + Me]** (~15 min, I drive)
So each report lands in a Drive folder automatically.

- [ ] 3.1 Create a **Google Cloud project** (console.cloud.google.com).
- [ ] 3.2 Enable the **Google Drive API** and **Google Sheets API**.
- [ ] 3.3 Create a **Service Account**, add a **JSON key**, download it.
- [ ] 3.4 Copy the service account's email (looks like
      `name@project.iam.gserviceaccount.com`).
- [ ] 3.5 For each customer: they **share a Drive folder** with that email
      (Editor), and paste the **folder ID** into their dashboard.
- [ ] 3.6 Give me the JSON — I set `GOOGLE_SERVICE_ACCOUNT_JSON` and test a real
      delivery.

## Phase 4 — Billing (Stripe products)  **[You + Me]** (~15 min, I drive)
- [ ] 4.1 In Stripe → Products, create one **recurring price per plan**
      (10/20/30/40/50/100 prospects/day). I'll give you the exact amounts.
- [ ] 4.2 Copy each **price ID** (`price_...`) → I map them to the plans.
- [ ] 4.3 Create a **webhook endpoint** pointing at your app; copy the
      **signing secret** → I wire activation to real payment.

## Phase 5 — Deploy  **[You + Me]** (~20 min, I drive)
- [ ] 5.1 Push the repo to your GitHub.
- [ ] 5.2 In Render: **New → Blueprint → pick the repo** (uses `render.yaml`:
      web service + daily cron + Postgres, all created automatically).
- [ ] 5.3 Paste the secret keys into Render (Apollo, Anthropic, Google, Stripe).
- [ ] 5.4 Point your **domain** at Render + enable HTTPS.

## Phase 6 — Test live end-to-end  **[Me + You]**
- [ ] 6.1 Flip `DATA_PROVIDER=apollo` and run **one small real search** (10
      prospects) — confirm real names/emails/phones come back and land in Drive.
- [ ] 6.2 Do a **test subscription** with a Stripe test card; confirm activation.
- [ ] 6.3 Confirm the **daily cron** fires and delivers with no duplicates.

## Phase 7 — Before you sell it  **[You + Me]**
- [ ] 7.1 **Privacy Policy + Terms** (required — you're handling personal contact
      data; consider GDPR/CAN‑SPAM notes). I can draft starters.
- [ ] 7.2 A **transactional email** (welcome + "your report is ready"). Needs an
      email sender (Resend/Postmark/SES) — I can add it.
- [ ] 7.3 Decide a **fair-use cap** so Apollo credits don't blow past your plan.
- [ ] 7.4 Sanity-check Apollo's terms on **redistributing contact data** to your
      customers for your use case.

---

### Rough monthly cost to run
- Apollo: **$49–99** · Anthropic: **~$5–30** (usage) · Render web+cron+Postgres:
  **~$21** · Domain: **~$1** · Stripe: **2.9% + 30¢** per charge.
- **≈ $80–150/mo** to operate before you have customers.

### Fastest path to a real first test
Do **Phase 2.1 (Apollo key)** + **Phase 3 (Google)**, and I'll run a real
10‑prospect report into your Drive **before** we bother with billing or hosting.
That proves the product with real data in under an hour.
