# 🚀 ProspectDaily — Launch Checklist

Everything is built and deployed. These are the final steps to go from "working"
to "taking real customers." **[You]** = your action · **[Me]** = ask Claude to do it.

---

## Phase 1 — Finish setup (~30 min)

### 1. Create your official account  **[You]** · 2 min
- [ ] Go to **prospectdaily.com/signup**, sign up with **`jaci@brandstateu.com`** + a password
- [ ] Go to **Plans → "Have a promo code?"** → enter **`jacibsu`** → **Redeem** (free Starter, 1 year)
- [ ] You'll now see an **Admin** link in the nav (cost tracker + all customers)

### 2. Set up email reply-forwarding  **[You]** · 5 min
So replies to your emails reach you.
- [ ] Namecheap → Domain List → prospectdaily.com → **Manage → Domain** (or Advanced DNS)
- [ ] Under **Mail Settings** (Email Forwarding is already on), add a forwarder:
      **`hello@`** → your real inbox (e.g. jaci@brandstateu.com)
- [ ] (Email domain verification in Resend is finishing on its own — no action needed)

### 3. Confirm Apollo's Terms  **[You]** · 10 min
- [ ] Check Apollo's Terms / API policy that **reselling/redistributing contact data
      to your customers** is allowed for your use case. If unsure, email Apollo support.
- [ ] This is the one legal item only you can verify — do it before charging.

### 4. Finish Stripe branding  **[You]** · 3 min *(in the ProspectDaily Stripe account)*
- [ ] **Settings → Business → Public details → Public business name → `ProspectDaily`**
      *(keep Legal business name = Brand State U for tax)*
- [ ] **Settings → Branding** → upload `prospectdaily-icon.png` (Icon) and
      `prospectdaily-logo.png` (Logo); brand color `#2563eb`

### 5. Activate Stripe for live payments  **[You] → [Me]**
- [ ] In the ProspectDaily Stripe account, complete **account activation**
      (business details + bank account) so you can accept real charges
- [ ] Then get your **live** secret key (Developers → API keys, test-mode toggled OFF, `sk_live_…`)
- [ ] **[Me]** Paste it to Claude → Claude creates live products/prices/webhook and
      swaps the keys on Render (~2 min). Now you're taking real money.

---

## Phase 2 — Verify before launch  **[Me + You]**
- [ ] After the live swap, do one **real** checkout (a small real charge, then refund
      it in Stripe) to confirm the full money loop, or trust the test-mode run we already verified
- [ ] Confirm the **daily cron** delivered (check your Drive folder tomorrow morning, or
      hit "Generate today's report now")
- [ ] Send yourself the **welcome email** test (Claude's watcher does this on Resend verify)

---

## Phase 3 — Launch
- [ ] **Friends first:** share promo codes (Karafree, Lizfree, Michaelfree, Mollyfree,
      Morganfree) — each is single-use, free Starter. Gather feedback.
- [ ] **Paying customers:** share prospectdaily.com. They sign up → build audience → pick a plan → pay.

---

## Ongoing operations
- **Watch Apollo credits** — admin dashboard shows month-to-date usage; you'll get an
  email alert at 80%. Upgrade your Apollo plan past ~5–6 active Starter customers.
- **Failure alerts** — if a daily run fails, you get an email automatically.
- **Costs** — ~$70/mo fixed; your first paying customer covers it many times over.
- **Deploys** — push to the `jacirusso/ProspectDaily` GitHub repo → Render auto-deploys.

---

### Who does what on the Stripe live swap
1. **You:** activate the account + get the `sk_live_…` key
2. **Claude:** run the product/price/webhook setup in live mode + set Render env vars + redeploy
3. **Done:** real payments flow to your bank
