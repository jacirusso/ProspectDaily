"""AI copywriting: for each prospect, generate (1) a short 'why this is a good
fit' rationale and (2) a personalized intro email.

Uses Anthropic's Claude when ANTHROPIC_API_KEY is set (stdlib urllib call, no
SDK dependency). Falls back to a solid template when no key is present, so the
pipeline always produces usable output for testing.
"""
import json
import urllib.request
import urllib.error
from typing import Tuple

from . import config


def _tone_line(tone: str) -> str:
    return {
        "Warm & consultative": "warm, helpful, and consultative",
        "Direct & concise": "direct and concise, no fluff",
        "Friendly & casual": "friendly and casual",
        "Formal & professional": "formal and professional",
    }.get(tone, "warm and professional")


def _fallback(prospect, answers) -> Tuple[str, str, str]:
    fit = (f"{prospect.company} is a {prospect.industry.lower()} company that fits "
           f"{answers.get('company_name','our')}'s ideal profile: {prospect.title} "
           f"is a typical decision-maker for {answers.get('value_prop','what we offer')}.")
    outcome = answers.get('value_prop', 'better results').rstrip('.').strip()
    industry = (prospect.industry or "your industry").lower()
    offer = answers.get('company_offer', '').rstrip('.').strip()
    if offer:
        offer = offer[0].lower() + offer[1:]   # reads mid-sentence after the dash
    subject = f"{prospect.company} + {answers.get('company_name','us')}"
    booking = answers.get("booking_link", "").strip()
    cta = (f"You can grab a time that works here: {booking}" if booking
           else "Open to a quick 15-minute call next week?")
    # Build a signature from whatever contact details were provided.
    title_co = ", ".join(x for x in [answers.get("sender_title", ""),
                                     answers.get("company_name", "")] if x)
    sig = [answers.get("sender_name", "")]
    if title_co:
        sig.append(title_co)
    for key in ("sender_email", "sender_phone", "company_website"):
        if answers.get(key):
            sig.append(answers[key])
    signature = "\n".join(s for s in sig if s)
    body = (
        f"Hi {prospect.first_name},\n\n"
        f"I came across {prospect.company} and your work leading marketing in the "
        f"{industry} space. I run {answers.get('company_name','our team')} — "
        f"{offer}, and we help companies like yours achieve {outcome}.\n\n"
        f"Given your role as {prospect.title}, I thought a quick conversation might "
        f"be worthwhile.\n\n"
        f"{cta}\n\n"
        f"Best,\n{signature}")
    linkedin = (
        f"Hi {prospect.first_name} — came across {prospect.company} and your work "
        f"in the {industry} space. I run {answers.get('company_name','our team')}; "
        f"we help teams like yours with {outcome}. Would love to connect.")
    return fit, subject, body, linkedin


def _claude(prospect, answers) -> Tuple[str, str, str]:
    tone = _tone_line(answers.get("email_tone", ""))
    prompt = f"""You are an expert B2B sales copywriter writing a cold intro email
for ONE specific prospect. The whole point is to PROVE you researched them, so
the email must reference a concrete, specific detail about THEIR company (from
the description/industry/size below) — not generic flattery.

OUR COMPANY: {answers.get('company_name','')}
WHAT WE SELL: {answers.get('company_offer','')}
THE #1 OUTCOME WE DELIVER: {answers.get('value_prop','')}
SIGNED BY: {answers.get('sender_name','')}, {answers.get('sender_title','')}
SIGNATURE CONTACT: email {answers.get('sender_email','')} · phone {answers.get('sender_phone','(none)')} · web {answers.get('company_website','(none)')}
BOOKING LINK (call-to-action): {answers.get('booking_link','') or '(none — ask for a short call instead)'}
DESIRED TONE: {tone}

THE PROSPECT (all real, verified data — use it, do not contradict it):
- Name: {prospect.full_name}  (use their first name)
- Title: {prospect.title}
- Company: {prospect.company}
- Industry: {prospect.industry}
- Size: ~{prospect.employee_count} employees
- What the company does: {prospect.company_description}
- Website: {prospect.website}

RULES:
- The FIRST sentence must show real research: name something specific about
  {prospect.company} (what they do, who they serve, their space) drawn ONLY from
  the data above. No made-up facts, metrics, funding, or news.
- Then connect that to the outcome we deliver, relevant to a {prospect.title}.
- Natural, human, {tone}. Under 120 words.
- One clear call-to-action: if a booking link is provided, invite them to grab a
  time at that link; otherwise ask for a short call. Include the raw link if given.
- Sound like a real person typed it, NOT like AI. Hard rules:
  * NEVER use an em-dash or en-dash (— or –) anywhere. Use a period or comma.
  * No AI-tells or clichés: "I hope this finds you well", "I wanted to reach out",
    "I couldn't help but notice", "As a leader in", "In today's fast-paced /
    ever-evolving landscape", "circling back", "synergy", "leverage", "seamless".
  * Plain words over fancy ones. Contractions are fine. Avoid perfectly balanced,
    overly polished sentences. Short and specific beats smooth.
- Correct grammar. End with a signature block: sender name, title, company, and
  the signature contact details provided above (only the ones that exist).

Return ONLY minified JSON with keys:
  "fit_reason": 1-2 sentences on why this company/person is a strong fit for us,
    citing a specific detail about them.
  "subject": a short, specific subject line (no clickbait, references their world).
  "body": the email, with real line breaks (\\n), signed by the sender.
  "linkedin_message": a LinkedIn connection note UNDER 280 characters (LinkedIn's
    limit) — casual, first-person, references something specific about their
    company, ends with a soft ask to connect. NO email-style signature block."""
    body = {
        "model": config.ANTHROPIC_MODEL,
        "max_tokens": 1100,
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode(), method="POST")
    req.add_header("content-type", "application/json")
    req.add_header("x-api-key", config.ANTHROPIC_API_KEY)
    req.add_header("anthropic-version", "2023-06-01")
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode())
    text = "".join(block.get("text", "") for block in data.get("content", []))
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[-1]
    parsed = json.loads(text)
    return (parsed.get("fit_reason", ""), parsed.get("subject", ""),
            parsed.get("body", ""), parsed.get("linkedin_message", ""))


def write_for(prospect, answers) -> None:
    """Fill fit_reason / intro_email_* / linkedin_message on the prospect."""
    try:
        if config.ANTHROPIC_API_KEY:
            fit, subject, body, linkedin = _claude(prospect, answers)
        else:
            fit, subject, body, linkedin = _fallback(prospect, answers)
    except Exception:
        fit, subject, body, linkedin = _fallback(prospect, answers)
    prospect.fit_reason = fit
    prospect.intro_email_subject = subject
    prospect.intro_email_body = body
    prospect.linkedin_message = linkedin
