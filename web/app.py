"""FastAPI web app: signup/login, the onboarding questionnaire, a customer
dashboard, plan selection (Stripe when configured, dev-activate otherwise), and
a 'run today's report now' action.

Run locally:
    ./.venv/bin/uvicorn web.app:app --reload --port 8000
"""
import logging
import os
import time
from datetime import date

from fastapi import FastAPI, Request, Form, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from engine import config, db
from engine.questionnaire import QUESTIONS, validate, blank_answers
from engine.pipeline import run_for_customer
from engine import dedupe, audience_builder
from web import store, security, plans, billing, emails, promos, crm

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("app")

BASE_DIR = os.path.dirname(__file__)
app = FastAPI(title="Prospect SaaS")
app.add_middleware(SessionMiddleware,
                   secret_key=os.environ.get("APP_SECRET_KEY", "dev-secret-change-me"))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


def _fmt_datetime(ts):
    import datetime
    if not ts:
        return ""
    return datetime.datetime.fromtimestamp(int(ts)).strftime("%b %d, %Y · %I:%M %p")


def _fmt_date(ts):
    import datetime
    if not ts:
        return ""
    return datetime.datetime.fromtimestamp(int(ts)).strftime("%b %d, %Y")


templates.env.filters["dt"] = _fmt_datetime
templates.env.filters["d"] = _fmt_date


@app.on_event("startup")
def _ensure_schema():
    """Create the database schema on boot so no request can race ahead of it."""
    try:
        db.init_schema()
        log.info("schema initialized")
    except Exception:
        log.exception("schema init on startup failed (will retry lazily)")


# --- helpers ------------------------------------------------------------
def current_user(request: Request):
    uid = request.session.get("uid")
    email = request.session.get("email")
    if uid and email:
        return {"id": uid, "email": email}
    return None


def require_user(request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return user


def _is_operator(user) -> bool:
    return bool(user and user["email"].lower() == config.OPERATOR_EMAIL.lower())


def _leaddaily_visible(user) -> bool:
    """LeadDaily is hidden from customers until launch: operator-only unless the
    LEADDAILY_PUBLIC flag is set."""
    return config.LEADDAILY_PUBLIC or _is_operator(user)


def render(request: Request, name: str, **ctx):
    user = current_user(request)
    ctx.update({"request": request, "user": user,
                "is_operator": _is_operator(user),
                "leaddaily_visible": _leaddaily_visible(user)})
    return templates.TemplateResponse(name, ctx)


# --- public pages -------------------------------------------------------
def _plan_rows():
    # Public pricing shows only what the customer buys (the ordered number).
    # Bonus + spare leads are a delight in the report, not advertised.
    return [{**p, "monthly": p["ordered"] * config.WEEKDAYS_PER_MONTH}
            for p in plans.PLANS]


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    if current_user(request):
        return RedirectResponse("/dashboard", status_code=303)
    return render(request, "home.html", plans=_plan_rows())


@app.get("/sample", response_class=HTMLResponse)
def sample(request: Request):
    """Public sample report so prospects can see what they'd receive."""
    return render(request, "sample.html")


@app.get("/signup", response_class=HTMLResponse)
def signup_form(request: Request):
    return render(request, "signup.html", error=None)


@app.post("/signup")
def signup(request: Request, email: str = Form(...), password: str = Form(...)):
    email = email.strip().lower()
    if len(password) < 8:
        return render(request, "signup.html",
                      error="Password must be at least 8 characters.")
    if store.get_user_by_email(email):
        return render(request, "signup.html", error="That email already has an account.")
    uid = store.create_user(email, security.hash_password(password))
    request.session.update({"uid": uid, "email": email})
    try:
        emails.send_welcome(store.get_customer(uid))
    except Exception:
        log.exception("welcome email failed")
    return RedirectResponse("/questionnaire", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return render(request, "login.html", error=None)


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    user = store.get_user_by_email(email.strip().lower())
    if not user or not security.verify_password(password, user["password_hash"]):
        return render(request, "login.html", error="Invalid email or password.")
    request.session.update({"uid": user["id"], "email": user["email"]})
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/forgot", response_class=HTMLResponse)
def forgot_form(request: Request):
    return render(request, "forgot.html", sent=False)


@app.post("/forgot", response_class=HTMLResponse)
def forgot(request: Request, email: str = Form(...)):
    user = store.get_user_by_email(email.strip().lower())
    if user:  # silently no-op for unknown emails (no account enumeration)
        token = store.create_password_reset(user["id"])
        base = os.environ.get("APP_BASE_URL", "https://prospectdaily.com").rstrip("/")
        try:
            emails.send_password_reset(user["email"], f"{base}/reset?token={token}")
        except Exception:
            log.exception("password reset email failed")
    return render(request, "forgot.html", sent=True)


@app.get("/reset", response_class=HTMLResponse)
def reset_form(request: Request, token: str = ""):
    valid = store.user_id_for_reset(token) is not None
    return render(request, "reset.html", token=token, valid=valid, error=None)


@app.post("/reset")
def reset(request: Request, token: str = Form(...), password: str = Form(...)):
    uid = store.user_id_for_reset(token)
    if not uid:
        return render(request, "reset.html", token=token, valid=False, error=None)
    if len(password) < 8:
        return render(request, "reset.html", token=token, valid=True,
                      error="Password must be at least 8 characters.")
    store.set_password(uid, security.hash_password(password))
    store.consume_reset(token)
    return RedirectResponse("/login?reset=1", status_code=303)


def _cost_summary(customers):
    """Live revenue / cost / margin estimate for the admin dashboard."""
    import datetime
    price = {p["key"]: p["price"] for p in plans.PLANS}
    active = [c for c in customers if c["status"] == "active"]
    paying = [c for c in active if c["plan"] in price]
    revenue = sum(price[c["plan"]] for c in paying)
    claude = sum(config.CLAUDE_COST_PER_10_PER_MONTH * (c["ordered_per_day"] / 10.0)
                 for c in active)
    stripe_fees = sum(price[c["plan"]] * config.STRIPE_PCT + config.STRIPE_FLAT
                      for c in paying)
    now = datetime.datetime.now(datetime.timezone.utc)
    month_start = int(datetime.datetime(now.year, now.month, 1,
                      tzinfo=datetime.timezone.utc).timestamp())
    records = store.prospects_delivered_since(month_start)
    data_cost = records * config.DATA_COST_PER_RECORD
    total_cost = config.MONTHLY_FIXED_COST + claude + stripe_fees + data_cost
    margin = revenue - total_cost
    return {
        "active": len(active), "paying": len(paying), "free": len(active) - len(paying),
        "revenue": round(revenue), "fixed": round(config.MONTHLY_FIXED_COST),
        "claude": round(claude, 2), "stripe": round(stripe_fees, 2),
        "data": round(data_cost, 2), "data_records": records,
        "data_rate": config.DATA_COST_PER_RECORD,
        "total_cost": round(total_cost), "margin": round(margin),
        "margin_pct": round(margin / revenue * 100) if revenue else 0,
    }


def _require_operator(request: Request):
    user = require_user(request)
    if user["email"].lower() != config.OPERATOR_EMAIL.lower():
        raise HTTPException(404, "Not found")
    return user


def _promo_rows():
    """Every promo code (built-in + admin-created) with redemption details."""
    import datetime
    from web import promos as pm
    reds = store.promo_redemptions()

    def fmt(ts):
        if not ts:
            return ""
        return datetime.datetime.fromtimestamp(
            int(ts), datetime.timezone.utc).strftime("%b %d, %Y")

    def row(code, info, source):
        r = reds.get(code)
        return {"code": code, "label": info.get("label", ""),
                "ordered_per_day": info.get("ordered_per_day", 10),
                "days": info.get("days") or 0, "source": source,
                "redeemed": bool(r), "redeemed_by": (r or {}).get("email") or "",
                "redeemed_when": fmt((r or {}).get("redeemed_at"))}

    rows = [row(c, i, "built-in") for c, i in pm.PROMO_CODES.items()]
    rows += [row(p["code"], p, "custom") for p in store.all_promo_codes()]
    # Redeemed first, then available; custom before built-in within each group.
    rows.sort(key=lambda r: (not r["redeemed"], r["source"] == "built-in", r["code"]))
    return rows


@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request):
    _require_operator(request)
    customers = store.admin_overview()
    promos_all = _promo_rows()
    return render(request, "admin.html", customers=customers,
                  costs=_cost_summary(customers), promos=promos_all,
                  promo_stats={"total": len(promos_all),
                               "redeemed": sum(1 for p in promos_all if p["redeemed"])})


@app.post("/admin/promo")
def admin_create_promo(request: Request, code: str = Form(...),
                       ordered_per_day: int = Form(10), days: int = Form(7),
                       label: str = Form("")):
    _require_operator(request)
    ok = store.create_promo_code(code, ordered_per_day, days, label)
    return RedirectResponse(f"/admin?promo={'added' if ok else 'exists'}#promos",
                            status_code=303)


@app.post("/admin/promo/delete")
def admin_delete_promo(request: Request, code: str = Form(...)):
    _require_operator(request)
    store.delete_promo_code(code)
    return RedirectResponse("/admin?promo=deleted#promos", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


# --- questionnaire ------------------------------------------------------
# --- AI Audience Builder ------------------------------------------------
@app.get("/audience", response_class=HTMLResponse)
def audience_form(request: Request):
    user = require_user(request)
    customer = store.get_customer(user["id"])
    website = (customer["answers"].get("company_website", "") if customer else "")
    return render(request, "audience.html", result=None, website=website, error=None)


@app.post("/audience", response_class=HTMLResponse)
def audience_run(request: Request, website: str = Form(...), offer: str = Form("")):
    user = require_user(request)
    customer = store.get_customer(user["id"])
    try:
        result = audience_builder.build(website.strip(), offer.strip())
    except Exception:
        log.exception("audience build failed")
        return render(request, "audience.html", result=None, website=website,
                      error="Couldn't read that website. Double-check the URL and try again.")
    for s in result["segments"]:
        s["pool_display"] = (f"{s['pool']:,}" if isinstance(s.get("pool"), int)
                             and s["pool"] >= 0 else "—")
    return render(request, "audience.html", result=result, website=website, error=None)


@app.post("/audience/choose")
async def audience_choose(request: Request):
    user = require_user(request)
    form = await request.form()
    customer = store.get_customer(user["id"])
    answers = dict(customer["answers"] or {})

    def split(name):
        return [p.strip() for p in (form.get(name) or "").split("||") if p.strip()]

    # company basics (from the site) — only fill if not already set
    for key, field in [("company_name", "company_name"),
                       ("company_offer", "company_offer"),
                       ("value_prop", "value_prop"),
                       ("company_website", "company_website")]:
        val = (form.get(field) or "").strip()
        if val:
            answers[key] = val
    # targeting from the chosen segment
    answers["target_titles"] = form.get("titles", "")
    answers["locations"] = form.get("locations", "")
    answers["keywords"] = form.get("keywords", "")
    answers["target_seniority"] = split("seniorities")
    answers["target_industries"] = split("industries")
    answers["company_size"] = split("company_size")
    store.save_answers(user["id"], answers)
    return RedirectResponse("/questionnaire", status_code=303)


@app.get("/questionnaire", response_class=HTMLResponse)
def questionnaire_form(request: Request):
    user = require_user(request)
    customer = store.get_customer(user["id"])
    answers = customer["answers"] or blank_answers()
    return render(request, "questionnaire.html",
                  questions=QUESTIONS, answers=answers, errors=[])


@app.post("/questionnaire")
async def questionnaire_submit(request: Request):
    user = require_user(request)
    form = await request.form()
    answers = {}
    for q in QUESTIONS:
        if q.kind == "multiselect":
            answers[q.key] = form.getlist(q.key)
        else:
            answers[q.key] = (form.get(q.key) or "").strip()
    errors = validate(answers)
    if errors:
        return render(request, "questionnaire.html",
                      questions=QUESTIONS, answers=answers, errors=errors)
    store.save_answers(user["id"], answers)
    return RedirectResponse("/dashboard", status_code=303)


# --- dashboard ----------------------------------------------------------
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    user = require_user(request)
    customer = store.get_customer(user["id"])
    runs = store.recent_runs(user["id"])
    plan = plans.by_key(customer["plan"]) if customer["plan"] else None
    is_promo = (customer["plan"] or "").startswith("promo:")
    upgrades = [p for p in plans.PLANS if p["ordered"] > customer["ordered_per_day"]]
    gen = customer.get("generating_since") or 0
    generating = bool(gen and time.time() - gen < 600)
    crm_enabled = bool(customer.get("crm_enabled"))
    crm_stats = None
    if crm_enabled:
        crm_stats = {"open": crm.stage_counts(user["id"])["open"],
                     "due": crm.due_today_count(user["id"]),
                     "available": crm.prospect_counts(user["id"])["available"]}
    return render(request, "dashboard.html",
                  customer=customer, runs=runs, plan=plan,
                  is_promo=is_promo, upgrades=upgrades,
                  total_delivered=dedupe.total_delivered(user["id"]),
                  generating=generating,
                  crm_enabled=crm_enabled, crm_stats=crm_stats,
                  leaddaily_price=plans.LEADDAILY["price"],
                  has_answers=bool(customer["answers"]))


@app.post("/settings/folder")
def set_folder(request: Request, folder_id: str = Form("")):
    user = require_user(request)
    store.update_customer(user["id"], folder_id=folder_id.strip())
    return RedirectResponse("/dashboard", status_code=303)


# --- billing / plans ----------------------------------------------------
@app.get("/plans", response_class=HTMLResponse)
def plans_page(request: Request):
    require_user(request)
    return render(request, "plans.html", plans=_plan_rows(),
                  stripe_enabled=plans.stripe_enabled())


@app.post("/plans/choose")
def choose_plan(request: Request, plan_key: str = Form(...),
                interval: str = Form("month")):
    user = require_user(request)
    plan = plans.by_key(plan_key)
    if not plan:
        raise HTTPException(400, "Unknown plan")
    interval = "year" if interval == "year" else "month"
    if plans.stripe_enabled():
        # Real checkout. Activation happens in the Stripe webhook AFTER payment.
        try:
            return RedirectResponse(billing.create_checkout_url(user, plan, interval),
                                    status_code=303)
        except Exception as e:
            log.exception("stripe checkout failed")
            raise HTTPException(500, f"Stripe error: {e}")
    # Dev mode (no Stripe configured): activate immediately without payment.
    store.update_customer(user["id"], plan=plan["key"],
                          ordered_per_day=plan["ordered"], status="active")
    return RedirectResponse("/dashboard", status_code=303)


@app.post("/upgrade")
def upgrade(request: Request, plan_key: str = Form(...)):
    user = require_user(request)
    plan = plans.by_key(plan_key)
    if not plan:
        raise HTTPException(400, "Unknown plan")
    customer = store.get_customer(user["id"])
    # Existing paying subscriber → modify their subscription in place (prorated).
    if plans.stripe_enabled() and customer.get("stripe_customer_id"):
        try:
            billing.change_plan(customer, plan)
            store.update_customer(user["id"], plan=plan["key"],
                                  ordered_per_day=plan["ordered"])
            return RedirectResponse("/dashboard?upgraded=1", status_code=303)
        except Exception:
            log.exception("plan change failed")
            return RedirectResponse("/plans", status_code=303)
    # No active subscription (promo/dev) → go through normal checkout to start paying.
    if plans.stripe_enabled():
        try:
            return RedirectResponse(billing.create_checkout_url(user, plan),
                                    status_code=303)
        except Exception:
            log.exception("upgrade checkout failed")
            return RedirectResponse("/plans", status_code=303)
    store.update_customer(user["id"], plan=plan["key"],
                          ordered_per_day=plan["ordered"], status="active")
    return RedirectResponse("/dashboard?upgraded=1", status_code=303)


@app.post("/redeem")
def redeem(request: Request, code: str = Form("")):
    user = require_user(request)
    promo = promos.lookup(code)
    if not promo:
        return RedirectResponse("/plans?promo=invalid", status_code=303)
    code_l = code.strip().lower()
    if not store.claim_promo(code_l, user["id"]):
        return RedirectResponse("/plans?promo=used", status_code=303)
    expires = (int(time.time()) + promo["days"] * 86400) if promo.get("days") else 0
    store.update_customer(user["id"], status="active", plan=f"promo:{code_l}",
                          ordered_per_day=promo["ordered_per_day"],
                          access_expires_at=expires)
    log.info("promo %s redeemed by %s", code_l, user["email"])
    return RedirectResponse("/dashboard?welcome=1", status_code=303)


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        result = billing.handle_webhook(payload, sig)
        return PlainTextResponse(result)
    except Exception as e:
        log.exception("stripe webhook error")
        raise HTTPException(400, f"Webhook error: {e}")


# --- account controls (pause / resume / cancel) -------------------------
@app.post("/account/pause")
def account_pause(request: Request):
    user = require_user(request)
    store.update_customer(user["id"], status="paused")
    return RedirectResponse("/dashboard", status_code=303)


@app.post("/account/resume")
def account_resume(request: Request):
    user = require_user(request)
    customer = store.get_customer(user["id"])
    if customer and customer.get("plan"):
        store.update_customer(user["id"], status="active")
    return RedirectResponse("/dashboard", status_code=303)


@app.post("/account/cancel")
def account_cancel(request: Request):
    user = require_user(request)
    customer = store.get_customer(user["id"])
    try:
        billing.cancel_subscription_for(customer)
    except Exception:
        log.exception("stripe cancel failed (continuing to mark canceled)")
    store.update_customer(user["id"], status="canceled", plan="")
    return RedirectResponse("/dashboard", status_code=303)


# --- LeadDaily (CRM add-on) ---------------------------------------------
def _parse_due(date_str: str) -> int:
    """A <input type=date> value ('YYYY-MM-DD') -> epoch at end of that day
    (server-local). Empty/invalid -> 0 (no due date)."""
    import datetime
    s = (date_str or "").strip()
    if not s:
        return 0
    try:
        d = datetime.date.fromisoformat(s)
        dt = datetime.datetime(d.year, d.month, d.day, 23, 59, 59)
        return int(time.mktime(dt.timetuple()))
    except Exception:
        return 0


def _require_leaddaily(request: Request):
    """LeadDaily is operator-only until launch — hide it entirely from customers,
    URL included, by 404-ing when it's not visible to this user."""
    user = require_user(request)
    if not _leaddaily_visible(user):
        raise HTTPException(404, "Not found")
    return user


def _require_crm(request: Request):
    """Gate every CRM route on the add-on being enabled. Not enabled -> bounce
    to the LeadDaily upsell page."""
    user = _require_leaddaily(request)
    customer = store.get_customer(user["id"])
    if not customer or not customer.get("crm_enabled"):
        raise HTTPException(status_code=302, headers={"Location": "/leaddaily"})
    return user, customer


@app.get("/leaddaily", response_class=HTMLResponse)
def leaddaily_page(request: Request):
    user = _require_leaddaily(request)
    customer = store.get_customer(user["id"])
    return render(request, "leaddaily.html", customer=customer,
                  price=plans.LEADDAILY["price"],
                  enabled=bool(customer and customer.get("crm_enabled")))


@app.post("/leaddaily/enable")
def leaddaily_enable(request: Request):
    user = _require_leaddaily(request)
    customer = store.get_customer(user["id"])
    if customer and customer.get("crm_enabled"):
        return RedirectResponse("/crm", status_code=303)
    # Real billing only when Stripe is live, a price is configured, and the
    # customer has a subscription to attach the add-on to. Otherwise dev/comp
    # enable (operator dogfood, promo accounts, local dev).
    if (plans.stripe_enabled() and plans.leaddaily_price_id()
            and customer and customer.get("stripe_customer_id")):
        try:
            billing.add_leaddaily(customer)
        except Exception:
            log.exception("LeadDaily add-on billing failed")
            return RedirectResponse("/leaddaily?error=1", status_code=303)
    store.update_customer(user["id"], crm_enabled=1)
    log.info("LeadDaily enabled for %s", user["email"])
    return RedirectResponse("/crm?welcome=1", status_code=303)


@app.post("/leaddaily/disable")
def leaddaily_disable(request: Request):
    user = _require_leaddaily(request)
    customer = store.get_customer(user["id"])
    try:
        billing.remove_leaddaily(customer)
    except Exception:
        log.exception("LeadDaily add-on removal failed (continuing to disable)")
    store.update_customer(user["id"], crm_enabled=0)
    return RedirectResponse("/leaddaily?disabled=1", status_code=303)


@app.get("/crm", response_class=HTMLResponse)
def crm_board(request: Request):
    user, customer = _require_crm(request)
    return render(request, "crm_board.html", customer=customer,
                  stages=crm.STAGES,
                  board=crm.leads_by_stage(user["id"]),
                  counts=crm.stage_counts(user["id"]),
                  due=crm.tasks_due(user["id"]),
                  prospect_counts=crm.prospect_counts(user["id"]),
                  now=int(time.time()))


@app.get("/crm/prospects", response_class=HTMLResponse)
def crm_prospects(request: Request):
    user, customer = _require_crm(request)
    return render(request, "crm_prospects.html", customer=customer,
                  prospects=crm.list_report_prospects(user["id"], limit=200),
                  counts=crm.prospect_counts(user["id"]))


@app.post("/crm/add/{prospect_id}")
def crm_add(request: Request, prospect_id: str):
    user, _ = _require_crm(request)
    lead_id = crm.add_lead_from_prospect(user["id"], prospect_id)
    if not lead_id:
        raise HTTPException(404, "Prospect not found")
    return RedirectResponse(f"/crm/lead/{lead_id}", status_code=303)


@app.get("/crm/lead/{lead_id}", response_class=HTMLResponse)
def crm_lead(request: Request, lead_id: str):
    user, customer = _require_crm(request)
    lead = crm.get_lead(user["id"], lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    return render(request, "crm_lead.html", customer=customer, lead=lead,
                  stages=crm.STAGES,
                  activities=crm.lead_activities(user["id"], lead_id),
                  tasks=crm.lead_tasks(user["id"], lead_id),
                  now=int(time.time()))


@app.post("/crm/lead/{lead_id}/stage")
def crm_lead_stage(request: Request, lead_id: str, stage: str = Form(...)):
    user, _ = _require_crm(request)
    crm.update_lead_stage(user["id"], lead_id, stage)
    back = "/crm" if stage in ("won", "lost") else f"/crm/lead/{lead_id}"
    return RedirectResponse(back, status_code=303)


@app.post("/crm/lead/{lead_id}/note")
def crm_lead_note(request: Request, lead_id: str, body: str = Form(...),
                  kind: str = Form("note")):
    user, _ = _require_crm(request)
    if not crm.get_lead(user["id"], lead_id):
        raise HTTPException(404, "Lead not found")
    if body.strip():
        crm.add_activity(user["id"], lead_id, kind, body)
    return RedirectResponse(f"/crm/lead/{lead_id}", status_code=303)


@app.post("/crm/lead/{lead_id}/value")
def crm_lead_value(request: Request, lead_id: str, value: str = Form("0")):
    user, _ = _require_crm(request)
    try:
        v = int(float((value or "0").replace(",", "").replace("$", "").strip()))
    except ValueError:
        v = 0
    crm.update_lead_value(user["id"], lead_id, v)
    return RedirectResponse(f"/crm/lead/{lead_id}", status_code=303)


@app.post("/crm/lead/{lead_id}/task")
def crm_lead_task(request: Request, lead_id: str, title: str = Form(...),
                  due: str = Form("")):
    user, _ = _require_crm(request)
    if not crm.get_lead(user["id"], lead_id):
        raise HTTPException(404, "Lead not found")
    if title.strip():
        crm.add_task(user["id"], lead_id, title, _parse_due(due))
    return RedirectResponse(f"/crm/lead/{lead_id}", status_code=303)


@app.post("/crm/lead/{lead_id}/delete")
def crm_lead_delete(request: Request, lead_id: str):
    user, _ = _require_crm(request)
    crm.delete_lead(user["id"], lead_id)
    return RedirectResponse("/crm", status_code=303)


@app.post("/crm/task/{task_id}/done")
def crm_task_done(request: Request, task_id: str, done: str = Form("1"),
                  back: str = Form("/crm")):
    user, _ = _require_crm(request)
    crm.complete_task(user["id"], task_id, done == "1")
    return RedirectResponse(back or "/crm", status_code=303)


@app.post("/crm/task/{task_id}/delete")
def crm_task_delete(request: Request, task_id: str, back: str = Form("/crm")):
    user, _ = _require_crm(request)
    crm.delete_task(user["id"], task_id)
    return RedirectResponse(back or "/crm", status_code=303)


# --- run a report now ---------------------------------------------------
def _generate_report_bg(user_id: str):
    """Generate a report in the background so the HTTP request returns instantly
    (report generation takes minutes and would otherwise time out at the proxy)."""
    today = date.today().isoformat()
    customer = store.get_customer(user_id)
    try:
        result = run_for_customer(
            user_id, customer["answers"], folder_id=customer["folder_id"],
            ordered=customer["ordered_per_day"], run_date=today)
        store.log_run(user_id, today, result.ordered, result.delivered,
                      result.csv_path, result.sheet_url)
        if result.folder_url:
            store.update_customer(user_id, client_folder_url=result.folder_url)
        if result.delivered:
            try:
                emails.send_first_report(store.get_customer(user_id),
                                         result.folder_url or "")
            except Exception:
                log.exception("first-report email failed")
    except Exception as e:
        log.exception("run-now failed for %s", user_id)
        store.log_run(user_id, today, customer["ordered_per_day"], 0, None, None,
                      status="error", error=str(e)[:500])
    finally:
        store.update_customer(user_id, generating_since=0)


@app.post("/run-now")
def run_now(request: Request, background_tasks: BackgroundTasks):
    user = require_user(request)
    customer = store.get_customer(user["id"])
    if not customer["answers"]:
        raise HTTPException(400, "Complete the questionnaire first.")
    # Don't start a second run while one is already in progress (< 10 min old).
    gen = customer.get("generating_since") or 0
    if not (gen and time.time() - gen < 600):
        store.update_customer(user["id"], generating_since=int(time.time()))
        background_tasks.add_task(_generate_report_bg, user["id"])
    return RedirectResponse("/dashboard?generating=1", status_code=303)


@app.get("/download/{run_id}")
def download(request: Request, run_id: str):
    user = require_user(request)
    for run in store.recent_runs(user["id"], limit=200):
        if run["id"] == run_id and run["csv_path"] and os.path.exists(run["csv_path"]):
            return FileResponse(run["csv_path"], filename=os.path.basename(run["csv_path"]))
    raise HTTPException(404, "Report not found")


@app.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request):
    return render(request, "privacy.html")


@app.get("/terms", response_class=HTMLResponse)
def terms(request: Request):
    return render(request, "terms.html")


@app.get("/healthz")
def healthz():
    return {"ok": True}
