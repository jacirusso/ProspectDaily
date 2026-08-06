"""People Data Labs provider (redistribution-licensed data).

Unlike Apollo's two-step model, PDL's Person Search returns FULL person records
in a single call — name, title, company, verified work email, phone, LinkedIn —
so there is no separate enrichment step. We hold ONE operator PDL account
(PDL_API_KEY) and deliver prospects to every customer; customers bring nothing.

Cost note: every record returned by Person Search is billable, so we dedupe the
returned records against the customer's ledger client-side (PDL has no free
preview like Apollo). Keep `want` modest.

⚠️  STAGED / UNVERIFIED: written against PDL's documented v5 Person Search API
but not yet smoke-tested against a live key. After signup, set PDL_API_KEY and
run `python -m engine.providers.pdl --selftest`, then reconcile field names and
work-email availability with the actual response before going live.

Docs: https://docs.peopledatalabs.com/docs/person-search-api
      https://docs.peopledatalabs.com/docs/fields
"""
import json
import sys
import time
import urllib.request
import urllib.error
from typing import List

from .base import Prospect, ProspectProvider
from .. import config


class PdlError(RuntimeError):
    pass


# Our (Apollo-style) seniority tokens -> PDL job_title_levels tokens.
_SENIORITY_TO_PDL = {
    "owner": "owner", "founder": "owner", "c_suite": "cxo", "partner": "partner",
    "vp": "vp", "director": "director", "manager": "manager",
    "senior": "senior", "entry": "entry",
}

# Our (Apollo-style) "min,max" employee ranges -> PDL job_company_size buckets.
_SIZE_TO_PDL = {
    "1,10": "1-10", "11,50": "11-50", "51,200": "51-200", "201,500": "201-500",
    "501,1000": "501-1000", "1001,5000": "1001-5000",
    "5001,10000": "5001-10000", "10001,1000000": "10001+",
}


def _post(path: str, body: dict, api_key: str) -> dict:
    url = f"{config.PDL_BASE_URL}{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Api-Key", api_key)
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        if e.code == 429:
            raise PdlError("PDL rate limit hit (429). Slow down or raise the plan.")
        if e.code in (401, 403):
            raise PdlError(f"PDL auth failed ({e.code}). Check PDL_API_KEY / plan "
                           f"access. {detail}")
        if e.code == 404:            # search: no matches
            return {"data": [], "total": 0}
        raise PdlError(f"PDL HTTP {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise PdlError(f"PDL network error: {e.reason}")


def _es_query(spec) -> dict:
    """Build an Elasticsearch bool query from a provider-neutral SearchSpec.
    Only records that HAVE a work email are returned (so we pay for usable ones)."""
    must = [{"exists": {"field": "work_email"}}]

    if spec.titles:
        must.append({"bool": {"minimum_should_match": 1, "should":
                     [{"match": {"job_title": t}} for t in spec.titles]}})

    levels = [_SENIORITY_TO_PDL[s] for s in spec.seniorities if s in _SENIORITY_TO_PDL]
    if levels:
        must.append({"terms": {"job_title_levels": levels}})

    sizes = [_SIZE_TO_PDL[s] for s in spec.employee_ranges if s in _SIZE_TO_PDL]
    if sizes:
        must.append({"terms": {"job_company_size": sizes}})

    if spec.locations:
        loc_should = []
        for loc in spec.locations:
            loc_should.append({"match": {"location_name": loc}})
            loc_should.append({"match": {"location_country": loc}})
        must.append({"bool": {"minimum_should_match": 1, "should": loc_should}})

    topic_terms = list(spec.industries) + list(spec.keywords)
    if topic_terms:
        topic_should = []
        for k in topic_terms:
            topic_should.append({"match": {"job_company_industry": k}})
            topic_should.append({"match": {"industry": k}})
            topic_should.append({"query_string":
                {"query": k, "default_operator": "AND",
                 "fields": ["job_title", "job_company_name", "summary", "skills"]}})
        must.append({"bool": {"minimum_should_match": 1, "should": topic_should}})

    query = {"bool": {"must": must}}
    if spec.exclude_keywords:
        must_not = []
        for k in spec.exclude_keywords:
            must_not.append({"match": {"job_company_name": k}})
            must_not.append({"match": {"job_title": k}})
            must_not.append({"match": {"job_company_industry": k}})
        query["bool"]["must_not"] = must_not
    return query


def _company_address(rec: dict) -> str:
    parts = [rec.get("job_company_location_street_address"),
             rec.get("job_company_location_locality"),
             rec.get("job_company_location_region"),
             rec.get("job_company_location_postal_code"),
             rec.get("job_company_location_country")]
    return ", ".join(p for p in parts if p)


def _pick_email(rec: dict) -> str:
    email = (rec.get("work_email") or "").strip()
    if email:
        return email
    for e in (rec.get("emails") or []):
        addr = (e.get("address") if isinstance(e, dict) else e) or ""
        etype = (e.get("type") if isinstance(e, dict) else "") or ""
        if addr and etype in ("current_professional", "professional", ""):
            return addr.strip()
    return ""


def _record_to_prospect(rec: dict) -> Prospect:
    li = (rec.get("linkedin_url") or "").strip()
    if li and not li.startswith("http"):
        li = "https://" + li
    phones = rec.get("phone_numbers") or []
    phone = ""
    if phones:
        phone = phones[0] if isinstance(phones[0], str) else (phones[0].get("number") or "")
    phone = phone or (rec.get("mobile_phone") or "")
    return Prospect(
        source="pdl",
        source_id=str(rec.get("id") or rec.get("pdl_id") or ""),
        first_name=rec.get("first_name") or "",
        last_name=rec.get("last_name") or "",
        title=rec.get("job_title") or "",
        email=_pick_email(rec),
        phone=phone,
        linkedin_url=li,
        company=rec.get("job_company_name") or "",
        website=rec.get("job_company_website") or "",
        company_address=_company_address(rec),
        company_description="",   # PDL has no long company description
        industry=rec.get("job_company_industry") or rec.get("industry") or "",
    )


class PdlProvider(ProspectProvider):
    def __init__(self, api_key: str):
        if not api_key:
            raise PdlError("No PDL API key configured (set PDL_API_KEY).")
        self.api_key = api_key

    def find(self, spec, limit: int, exclude_keys: set) -> List[Prospect]:
        query = _es_query(spec)
        want = max(limit, limit * 3)
        size = min(100, max(10, want))
        body = {"query": query, "size": size, "dataset": "all"}
        out: List[Prospect] = []
        seen = set(exclude_keys)
        token = None
        pages = 0
        while len(out) < limit and pages < 6:
            if token:
                body["scroll_token"] = token
            resp = _post("/person/search", body, self.api_key)
            data = resp.get("data") or []
            if not data:
                break
            for rec in data:
                p = _record_to_prospect(rec)
                if not p.email or p.is_duplicate(seen):
                    continue
                seen.update(p.identity_keys())
                out.append(p)
                if len(out) >= limit:
                    break
            token = resp.get("scroll_token")
            if not token:
                break
            pages += 1
            time.sleep(0.3)
        return out


def validate_key(api_key: str) -> bool:
    """Return True if the PDL key works (a cheap size:1 search)."""
    if not api_key:
        return False
    try:
        _post("/person/search",
              {"query": {"bool": {"must": [{"exists": {"field": "work_email"}}]}},
               "size": 1, "dataset": "all"}, api_key)
        return True
    except PdlError:
        return False


if __name__ == "__main__":               # python -m engine.providers.pdl --selftest
    if "--selftest" in sys.argv:
        from ..segments import spec_from_answers
        key = config.PDL_API_KEY
        print("key valid:", validate_key(key))
        spec = spec_from_answers({
            "target_titles": "VP of Marketing, CMO, Head of Growth",
            "target_seniority": ["VP", "C-Suite"],
            "company_size": ["51-200", "201-500"],
            "target_industries": ["Software / SaaS"],
            "locations": "United States",
        })
        got = PdlProvider(key).find(spec, limit=3, exclude_keys=set())
        for p in got:
            print(f"  {p.full_name} | {p.title} @ {p.company} | {p.email} | {p.phone}")
        print(f"returned {len(got)} record(s)")
