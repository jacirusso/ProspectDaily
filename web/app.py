"""FastAPI web app: signup/login, the onboarding questionnaire, a customer
dashboard, plan selection (Stripe when configured, dev-activate otherwise), and
a 'run today's report now' action.

Run locally:
    ./.venv/bin/uvicorn web.app:app --reload --port 8000
"""
import logging
import os
from datetime import date

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from engine import config
from engine.questionnaire import QUESTIONS, validate, blank_answers
from engine.pipeline import run_for_customer
from engine import dedupe
from web import store, security, plans, billing

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


def render(request: Request, name: str, **ctx):
    ctx.update({"request": request, "user": current_user(request)})
    return templates.TemplateResponse(name, ctx)


# --- public pages -------------------------------------------------------
def _plan_rows():
    rows = []
    for p in plans.PLANS:
        delivered = config.delivered_for(p["ordered"])
        rows.append({**p, "delivered": delivered,
                     "monthly": delivered * config.WEEKDAYS_PER_MONTH})
    return rows


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    if current_user(request):
        return RedirectResponse("/dashboard", status_code=303)
    return render(request, "home.html", plans=_plan_rows())


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


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


# --- questionnaire ------------------------------------------------------
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
    return render(request, "dashboard.html",
                  customer=customer, runs=runs, plan=plan,
                  total_delivered=dedupe.total_delivered(user["id"]),
                  overdeliver=config.OVERDELIVER_MULTIPLIER,
                  delivered_per_day=config.delivered_for(customer["ordered_per_day"]),
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
                  overdeliver=config.OVERDELIVER_MULTIPLIER,
                  stripe_enabled=plans.stripe_enabled())


@app.post("/plans/choose")
def choose_plan(request: Request, plan_key: str = Form(...)):
    user = require_user(request)
    plan = plans.by_key(plan_key)
    if not plan:
        raise HTTPException(400, "Unknown plan")
    if plans.stripe_enabled():
        # Real checkout. Activation happens in the Stripe webhook AFTER payment.
        try:
            return RedirectResponse(billing.create_checkout_url(user, plan),
                                    status_code=303)
        except Exception as e:
            log.exception("stripe checkout failed")
            raise HTTPException(500, f"Stripe error: {e}")
    # Dev mode (no Stripe configured): activate immediately without payment.
    store.update_customer(user["id"], plan=plan["key"],
                          ordered_per_day=plan["ordered"], status="active")
    return RedirectResponse("/dashboard", status_code=303)


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


# --- run a report now ---------------------------------------------------
@app.post("/run-now")
def run_now(request: Request):
    user = require_user(request)
    customer = store.get_customer(user["id"])
    if not customer["answers"]:
        raise HTTPException(400, "Complete the questionnaire first.")
    today = date.today().isoformat()
    try:
        result = run_for_customer(
            user["id"], customer["answers"],
            folder_id=customer["folder_id"],
            ordered=customer["ordered_per_day"],
            run_date=today)
        store.log_run(user["id"], today, result.ordered, result.delivered,
                      result.csv_path, result.sheet_url)
        if result.folder_url:
            store.update_customer(user["id"], client_folder_url=result.folder_url)
    except Exception as e:
        log.exception("run-now failed for %s", user["id"])
        store.log_run(user["id"], today, customer["ordered_per_day"], 0,
                      None, None, status="error", error=str(e)[:500])
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/download/{run_id}")
def download(request: Request, run_id: str):
    user = require_user(request)
    for run in store.recent_runs(user["id"], limit=200):
        if run["id"] == run_id and run["csv_path"] and os.path.exists(run["csv_path"]):
            return FileResponse(run["csv_path"], filename=os.path.basename(run["csv_path"]))
    raise HTTPException(404, "Report not found")


@app.get("/healthz")
def healthz():
    return {"ok": True}
