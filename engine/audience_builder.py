"""AI Audience Builder.

Turn a customer's website into 2-4 ready-to-use target-audience segments, each
scored and sized:

  1. fetch_site()      -> pull readable text from their homepage (+ /about).
  2. propose_segments() -> Claude reads it and proposes distinct segments, each
     with concrete criteria (titles/industries/size/geo/keywords), a fit score,
     and a rationale. Values are constrained to our questionnaire's vocabulary
     so a chosen segment maps straight onto a customer's targeting.
  3. pool_count()      -> a FREE Apollo search (no reveal credits) returns how
     many prospects actually exist for the segment, so the score is grounded.

The customer then picks the segment to pursue.
"""
import json
import re
import urllib.request
import urllib.error

from . import config
from .segments import SearchSpec, spec_to_apollo_params

# Allowed vocabularies (must match engine/questionnaire.py so segments map back).
SENIORITIES = ["Owner", "Founder", "C-Suite", "Partner", "VP",
               "Head / Director", "Manager", "Senior", "Entry"]
INDUSTRIES = ["Software / SaaS", "Marketing & Advertising", "Financial Services",
              "Healthcare", "Manufacturing", "Real Estate", "Construction",
              "Retail / E-commerce", "Professional Services", "Education",
              "Hospitality", "Nonprofit", "Legal", "Logistics & Supply Chain"]
SIZES = ["1-10", "11-50", "51-200", "201-500", "501-1000", "1001-5000",
         "5001-10000", "10001+"]


# --- 1. website -> text --------------------------------------------------
def _strip_html(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"&[a-z#0-9]+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _get(url: str, timeout: int = 20) -> str:
    if not url.startswith("http"):
        url = "https://" + url
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 ProspectDaily"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def _candidate_bases(url: str):
    """URLs to try, including the www/non-www variant (many domains only
    resolve one way — e.g. brandstateu.com fails, www.brandstateu.com works)."""
    from urllib.parse import urlparse
    if not url.startswith("http"):
        url = "https://" + url
    p = urlparse(url)
    host = p.netloc
    hosts = [host, host[4:] if host.startswith("www.") else "www." + host]
    out, seen = [], set()
    for h in hosts:
        base = f"{p.scheme}://{h}"
        if h and base not in seen:
            seen.add(base)
            out.append(base)
    return out


def fetch_site(url: str, max_chars: int = 6000) -> str:
    """Readable text from the homepage (trying www variants), plus /about pages
    if the homepage is thin. Returns '' if the site can't be read."""
    text, root = "", None
    for base in _candidate_bases(url):
        try:
            candidate = _strip_html(_get(base))
        except Exception:
            continue
        if len(candidate) >= 300:
            text, root = candidate, base
            break
    if root and len(text) < 1500:
        for path in ("/about", "/about-us", "/what-we-do", "/services"):
            try:
                text += " " + _strip_html(_get(root + path))
            except Exception:
                continue
            if len(text) > 2500:
                break
    return text[:max_chars]


# --- 2. text -> segments (Claude) ---------------------------------------
def _claude_json(prompt: str) -> dict:
    body = {"model": config.ANTHROPIC_MODEL, "max_tokens": 1800,
            "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request("https://api.anthropic.com/v1/messages",
                                 data=json.dumps(body).encode(), method="POST")
    req.add_header("content-type", "application/json")
    req.add_header("x-api-key", config.ANTHROPIC_API_KEY)
    req.add_header("anthropic-version", "2023-06-01")
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode())
    text = "".join(b.get("text", "") for b in data.get("content", [])).strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[-1]
        if text.lower().startswith("json"):
            text = text[4:]
    return json.loads(text)


def propose_segments(site_text: str, offer_hint: str = "") -> list:
    prompt = f"""You are a B2B go-to-market strategist. From the company website
content below, propose EXACTLY 3 DISTINCT target-audience segments this company
could prospect into for outbound sales. Make them genuinely different from each
other (different industries/roles), realistic, and reachable at scale.

COMPANY WEBSITE CONTENT:
{site_text}

{("EXTRA CONTEXT FROM THE OWNER: " + offer_hint) if offer_hint else ""}

Choose values ONLY from these allowed lists (exact strings):
- seniorities: {SENIORITIES}
- industries: {INDUSTRIES}
- company_size: {SIZES}

Also extract the company's own basics from the site so we can pre-fill onboarding.

Return ONLY minified JSON of this shape:
{{"company":{{
  "name":"the company's name",
  "sells":"one sentence: what they sell / do",
  "outcome":"the #1 outcome/result they deliver to customers"
}},
"segments":[{{
  "name":"short segment name",
  "who":"one sentence describing who they are",
  "titles":["3-6 specific job titles to target"],
  "seniorities":["subset of the allowed seniorities"],
  "industries":["1-3 from the allowed industries"],
  "company_size":["1-4 of the allowed bands"],
  "locations":["real places like states or countries; [] means anywhere"],
  "keywords":["2-5 company keywords describing a great-fit account"],
  "fit_score": <integer 0-100, how strong a fit this segment is for the company>,
  "rationale":"one sentence on why this segment is promising"
}}]}}"""
    data = _claude_json(prompt)
    company = data.get("company", {}) or {}
    segs = data.get("segments", [])[:4]
    # sanitize to allowed vocab
    for s in segs:
        s["seniorities"] = [x for x in s.get("seniorities", []) if x in SENIORITIES]
        s["industries"] = [x for x in s.get("industries", []) if x in INDUSTRIES]
        s["company_size"] = [x for x in s.get("company_size", []) if x in SIZES]
        s["titles"] = [t for t in s.get("titles", []) if t][:6]
        s["locations"] = [l for l in s.get("locations", []) if l][:8]
        s["keywords"] = [k for k in s.get("keywords", []) if k][:6]
        try:
            s["fit_score"] = max(0, min(100, int(s.get("fit_score", 0))))
        except Exception:
            s["fit_score"] = 0
    return {"company": company, "segments": segs}


# --- 3. segment -> available pool (free Apollo search) -------------------
def _spec_from_segment(seg: dict) -> SearchSpec:
    from .segments import _SENIORITY_MAP, _SIZE_MAP
    return SearchSpec(
        titles=seg.get("titles", []),
        seniorities=[_SENIORITY_MAP[s] for s in seg.get("seniorities", []) if s in _SENIORITY_MAP],
        industries=seg.get("industries", []),
        employee_ranges=[_SIZE_MAP[s] for s in seg.get("company_size", []) if s in _SIZE_MAP],
        locations=seg.get("locations", []),
        keywords=seg.get("keywords", []),
    )


def pool_count(seg: dict) -> int:
    """Approx. number of matching prospects Apollo has (free; no reveal credits).
    Returns -1 if unavailable (e.g., mock mode or API error)."""
    if config.DATA_PROVIDER.lower() != "apollo" or not config.APOLLO_API_KEY:
        return -1
    from .providers.apollo import _post, ApolloError
    spec = _spec_from_segment(seg)
    params = spec_to_apollo_params(spec, page=1, per_page=1)
    try:
        resp = _post("/mixed_people/api_search", params)
    except ApolloError:
        return -1
    # api_search returns the match count at the TOP LEVEL as total_entries.
    total = resp.get("total_entries")
    if total is None:
        total = (resp.get("pagination") or {}).get("total_entries")
    if total is None:
        total = len(resp.get("people") or [])
    return int(total)


def build(url: str, offer_hint: str = "") -> dict:
    """Full flow: returns {site_chars, company:{...}, segments:[...with pool...]}.
    Returns {"insufficient": True} when the site couldn't be read AND no
    description was given — so we never invent a generic audience."""
    site = fetch_site(url)
    if len(site) < 300 and len(offer_hint.strip()) < 40:
        return {"insufficient": True, "site_chars": len(site),
                "company": {}, "segments": []}
    analysis = propose_segments(site, offer_hint)
    segments = analysis["segments"]
    for s in segments:
        s["pool"] = pool_count(s)
    segments.sort(key=lambda s: (s.get("fit_score", 0)), reverse=True)
    return {"site_chars": len(site), "company": analysis["company"],
            "segments": segments}
