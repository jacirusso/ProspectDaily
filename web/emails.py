"""The lifecycle / welcome email series.

Each email is idempotent (tracked in sent_emails), so triggers can fire freely:
  - welcome        : on signup (immediate)
  - nudge_d1       : ~1 day later if they haven't activated a plan
  - first_report   : when their first report is delivered
  - tips_d4        : ~4 days after signup, for active customers

Emails render as inline-styled, table-based HTML (email-client safe) in the
blue/green brand. Sending goes through engine.emailer (Resend, or dev outbox).
"""
import os
import time

from engine import emailer
from web import store

_BASE = os.environ.get("APP_BASE_URL", "https://prospectdaily.com").rstrip("/")
_BRAND = "#2563eb"; _GREEN = "#059669"; _INK = "#14161f"; _MUTED = "#6b7280"
DAY = 86400


def _name(customer) -> str:
    ans = customer.get("answers") or {}
    return (ans.get("sender_name") or (customer.get("email", "").split("@")[0])
            or "there")


def _button(href, label):
    return (f'<a href="{href}" style="display:inline-block;background:{_BRAND};'
            f'color:#ffffff;text-decoration:none;padding:12px 24px;border-radius:8px;'
            f'font-weight:bold;font-size:15px;">{label}</a>')


def _wrap(preheader, body):
    return f"""<div style="background:#f4f6fb;padding:24px 12px;font-family:Arial,Helvetica,sans-serif;">
<span style="display:none;max-height:0;overflow:hidden;">{preheader}</span>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
<table role="presentation" width="560" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border:1px solid #e5e7eb;border-radius:14px;overflow:hidden;">
  <tr><td style="padding:20px 28px;border-bottom:1px solid #eef2f7;">
    <span style="font-size:20px;font-weight:bold;color:{_INK};">Prospect<span style="color:{_GREEN};">Daily</span></span>
  </td></tr>
  <tr><td style="padding:28px;color:{_INK};font-size:15px;line-height:1.6;">{body}</td></tr>
  <tr><td style="padding:18px 28px;background:#f9fafb;color:{_MUTED};font-size:12px;line-height:1.5;">
    ProspectDaily — fresh B2B prospects, every weekday.<br>
    <a href="{_BASE}" style="color:{_MUTED};">prospectdaily.com</a>
  </td></tr>
</table></td></tr></table></div>"""


# --- the emails ---------------------------------------------------------
def _welcome(c):
    body = f"""<h1 style="margin:0 0 14px;font-size:23px;">Welcome to ProspectDaily 👋</h1>
<p>Hi {_name(c)},</p>
<p>You're in! Here's the idea: tell us who your best customers are, and every
weekday we deliver fresh, <strong>verified</strong> B2B prospects — with email,
phone, LinkedIn, a company profile, why they're a fit, and a ready-to-send intro
email — straight into your Google Drive.</p>
<p><strong>Your next step</strong> takes 15 seconds: let our AI read your website
and suggest your best-fit target audiences.</p>
<p style="margin:24px 0;">{_button(_BASE + "/audience", "✨ Build my audience")}</p>
<p>Questions? Just reply — a real person reads these.</p>"""
    return "Welcome to ProspectDaily 🎯", _wrap("Let's find your best prospects.", body)


def _nudge(c):
    body = f"""<h1 style="margin:0 0 14px;font-size:22px;">You're one step from your first prospects</h1>
<p>Hi {_name(c)},</p>
<p>Your account's ready, but we're not delivering yet. Two quick things and
you'll wake up to fresh prospects every weekday:</p>
<p>1. <a href="{_BASE}/audience" style="color:{_BRAND};">Build your target audience</a> (our AI does the hard part)<br>
2. <a href="{_BASE}/plans" style="color:{_BRAND};">Pick a plan</a></p>
<p style="margin:24px 0;">{_button(_BASE + "/dashboard", "Finish setup")}</p>"""
    return "Your first prospects are one step away", _wrap("Finish setup in 2 minutes.", body)


def _first_report(c, folder_url):
    cta = _button(folder_url or (_BASE + "/dashboard"), "📁 Open my prospects")
    body = f"""<h1 style="margin:0 0 14px;font-size:23px;">Your first prospects are ready 🎯</h1>
<p>Hi {_name(c)},</p>
<p>Your first daily report just landed in your Google Drive — real, verified
prospects, each with a ready-to-send intro email. Fresh ones arrive every
weekday from here on.</p>
<p style="margin:24px 0;">{cta}</p>
<p>Tip: the intro emails are written to reference each company specifically —
copy, tweak a line if you like, and send.</p>"""
    return "🎯 Your first ProspectDaily prospects are ready", _wrap("Your prospects have arrived.", body)


def _tips(c):
    body = f"""<h1 style="margin:0 0 14px;font-size:22px;">Getting the most out of ProspectDaily 💡</h1>
<p>Hi {_name(c)},</p>
<p>A few days in — here's how power users get results:</p>
<ul>
  <li><strong>Send within a day.</strong> Fresh prospects respond best while they're new.</li>
  <li><strong>Personalize one line.</strong> The intro emails are 90% there; a single tailored sentence lifts replies.</li>
  <li><strong>Tune your audience anytime</strong> under <a href="{_BASE}/questionnaire" style="color:{_BRAND};">Target Audience</a> — narrower isn't always better.</li>
  <li><strong>Watch the "Why it's a fit" note</strong> — it's your opener on a call.</li>
</ul>
<p style="margin:22px 0;">{_button(_BASE + "/dashboard", "Go to my dashboard")}</p>
<p>Reply anytime if you want a hand dialing in your targeting.</p>"""
    return "💡 3 ways to get more from your daily prospects", _wrap("Make your prospects count.", body)


def _trial_ending(c, days_left):
    d = max(1, int(round(days_left)))
    body = f"""<h1 style="margin:0 0 14px;font-size:22px;">Your free trial ends in {d} day{'s' if d != 1 else ''}</h1>
<p>Hi {_name(c)},</p>
<p>You've been getting fresh, verified prospects every weekday — with a ready-to-send
intro email for each. In about {d} day{'s' if d != 1 else ''} your free trial wraps
up and deliveries stop.</p>
<p>Keep the momentum going: pick a plan and <strong>nothing changes</strong> — same
audience, same daily delivery, no gap. Your targeting and your full "already-received"
list stay saved, so you keep getting only net-new prospects.</p>
<p style="margin:24px 0;">{_button(_BASE + "/plans", "Choose my plan")}</p>
<p>Questions before you decide? Just reply — a real person reads these.</p>"""
    return (f"⏳ {d} day{'s' if d != 1 else ''} left on your ProspectDaily trial",
            _wrap("Keep your daily prospects coming.", body))


def _trial_lastday(c):
    body = f"""<h1 style="margin:0 0 14px;font-size:22px;">Today's your last free report</h1>
<p>Hi {_name(c)},</p>
<p>Your ProspectDaily trial ends today. Subscribe now and fresh prospects keep
landing every weekday — same audience you dialed in, no interruption.</p>
<p style="margin:24px 0;">{_button(_BASE + "/plans", "Keep my prospects coming")}</p>
<p>Not ready? No worries — your targeting stays saved if you come back later.</p>"""
    return ("Today's your last free ProspectDaily report",
            _wrap("Your trial ends today.", body))


def _trial_ended(c):
    body = f"""<h1 style="margin:0 0 14px;font-size:22px;">Your trial has ended — reactivate anytime</h1>
<p>Hi {_name(c)},</p>
<p>Your free trial wrapped up, so daily deliveries are paused. The good news: your
target audience and the full list of everyone you've already received are
<strong>saved</strong>. Subscribe and you pick right back up with net-new prospects
the next weekday — nothing to set up again.</p>
<p style="margin:24px 0;">{_button(_BASE + "/plans", "Reactivate my account")}</p>
<p>Curious what you'd get? Reply and I'll send a sample report.</p>"""
    return ("Your ProspectDaily trial ended — reactivate anytime",
            _wrap("Pick up right where you left off.", body))


# --- triggers -----------------------------------------------------------
def _send_once(customer, key, subject, html) -> bool:
    if store.email_sent(customer["id"], key):
        return False
    ok = emailer.send(customer["email"], subject, html)
    if ok:
        store.mark_email_sent(customer["id"], key)
    return ok


def send_password_reset(email: str, link: str):
    from engine import emailer
    body = f"""<h1 style="margin:0 0 14px;font-size:22px;">Reset your password</h1>
<p>We got a request to reset your ProspectDaily password. Click below to set a
new one — this link expires in 1 hour.</p>
<p style="margin:24px 0;">{_button(link, "Reset my password")}</p>
<p style="color:{_MUTED};font-size:13px;">If you didn't request this, you can
safely ignore this email — your password won't change.</p>"""
    emailer.send(email, "Reset your ProspectDaily password",
                 _wrap("Reset your password", body))


def send_welcome(customer):
    s, h = _welcome(customer)
    return _send_once(customer, "welcome", s, h)


def send_first_report(customer, folder_url=""):
    s, h = _first_report(customer, folder_url)
    return _send_once(customer, "first_report", s, h)


def alert_operator(subject: str, body_html: str):
    """Send an operational alert to the operator (failed runs, low credits)."""
    from engine import config, emailer
    html = _wrap(subject, f"<h2 style='margin:0 0 12px;'>{subject}</h2>{body_html}")
    emailer.send(config.OPERATOR_EMAIL, f"[ProspectDaily] {subject}", html)


def run_lifecycle():
    """Called by the daily job: send day-1 nudges, day-4 tips, and the
    free-trial conversion series (as their trial nears / passes expiry)."""
    now = int(time.time())
    sent = 0
    for c in store.all_customers():
        age = now - int(c.get("created_at") or now)
        if DAY <= age < 5 * DAY and c.get("status") != "active":
            s, h = _nudge(c)
            if _send_once(c, "nudge_d1", s, h):
                sent += 1
        if age >= 4 * DAY and c.get("status") == "active":
            s, h = _tips(c)
            if _send_once(c, "tips_d4", s, h):
                sent += 1

        # Free-trial conversion series. Targets SHORT promo trials only (a set
        # expiry within ~31 days — i.e. the 7-day test codes, not year-long
        # comps). Fires once each as the trial nears, hits, and passes expiry.
        exp = int(c.get("access_expires_at") or 0)
        created = int(c.get("created_at") or 0)
        is_promo = (c.get("plan") or "").startswith("promo:")
        is_short_trial = is_promo and exp and created and (exp - created) <= 31 * DAY
        if is_short_trial:
            days_left = (exp - now) / DAY
            if 1.5 < days_left <= 3.5:
                s, h = _trial_ending(c, days_left)
                if _send_once(c, "trial_ending", s, h):
                    sent += 1
            elif 0 <= days_left <= 1.5:
                s, h = _trial_lastday(c)
                if _send_once(c, "trial_lastday", s, h):
                    sent += 1
            elif days_left < 0:
                s, h = _trial_ended(c)
                if _send_once(c, "trial_ended", s, h):
                    sent += 1
    return sent
