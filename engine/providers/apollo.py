"""Real Apollo.io provider using only the standard library (urllib).

Apollo's API is a TWO-STEP model, and this adapter is built around it to spend
credits only where it must:

  1. POST /mixed_people/api_search  -> a PREVIEW of matching people: first name,
     title, company name, a stable person id, and boolean flags (has_email,
     has_direct_phone). No contact details, and it costs no reveal credits.
     We filter here (dedupe by person id, drop no-email records, keyword
     excludes on title/company) so we never waste an enrichment credit.

  2. POST /people/match (per kept person) -> full profile + a VERIFIED EMAIL.
     This costs ~1 credit each. We only enrich people we intend to deliver.

Phones: Apollo returns direct/mobile numbers ASYNCHRONOUSLY to a webhook, not in
the match response, and from a separate mobile-credit bucket. We request them
only when APOLLO_REVEAL_PHONE is on and a webhook URL is configured.

Docs: https://docs.apollo.io/reference/people-api-search
      https://docs.apollo.io/reference/people-enrichment
"""
import json
import time
import urllib.request
import urllib.error
from typing import List, Optional

from .base import Prospect, ProspectProvider
from .. import config
from ..segments import spec_to_apollo_params


class ApolloError(RuntimeError):
    pass


def _post(path: str, body: dict, api_key: str) -> dict:
    url = f"{config.APOLLO_BASE_URL}{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Cache-Control", "no-cache")
    req.add_header("X-Api-Key", api_key)
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        if e.code == 429:
            raise ApolloError("Apollo rate limit hit (429). Slow down or upgrade plan.")
        if e.code in (401, 403):
            raise ApolloError(f"Apollo auth failed ({e.code}). Check the API key / "
                              f"that API access is enabled on your plan. {detail}")
        raise ApolloError(f"Apollo HTTP {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise ApolloError(f"Apollo network error: {e.reason}")


def _org_address(org: dict) -> str:
    parts = [org.get("street_address"), org.get("city"),
             org.get("state"), org.get("postal_code"), org.get("country")]
    return ", ".join(p for p in parts if p)


def _preview_to_prospect(sp: dict) -> Prospect:
    """Build a light Prospect from a search-preview record (no contact info)."""
    org = sp.get("organization") or {}
    return Prospect(
        source="apollo",
        source_id=str(sp.get("id", "")),
        first_name=sp.get("first_name", "") or "",
        title=sp.get("title", "") or "",
        company=org.get("name", "") or "",
    )


def _apply_enrichment(p: Prospect, person: dict) -> None:
    """Fill a prospect from a /people/match response (full profile + email)."""
    org = person.get("organization") or {}
    name = person.get("name") or ""
    if name and not p.last_name:
        parts = name.split()
        p.first_name = p.first_name or (parts[0] if parts else "")
        p.last_name = " ".join(parts[1:]) if len(parts) > 1 else p.last_name
    p.title = person.get("title") or p.title
    email = (person.get("email") or "").strip()
    if email and "email_not_unlocked" not in email.lower():
        p.email = email
    p.linkedin_url = person.get("linkedin_url") or p.linkedin_url
    # Phone: prefer the person's direct number if present, else fall back to the
    # company's main office line (org data, no extra credit / no webhook needed).
    phones = person.get("phone_numbers") or []
    if phones and phones[0].get("raw_number"):
        p.phone = phones[0]["raw_number"]
    else:
        primary = org.get("primary_phone") or {}
        p.phone = (primary.get("number") or org.get("phone")
                   or org.get("sanitized_phone") or p.phone)
    p.company = org.get("name") or p.company
    p.website = org.get("website_url") or org.get("primary_domain") or p.website
    p.company_address = _org_address(org) or p.company_address
    p.company_description = (org.get("short_description")
                             or org.get("seo_description") or "")[:600] or p.company_description
    p.industry = org.get("industry") or p.industry
    p.employee_count = org.get("estimated_num_employees") or p.employee_count


class ApolloProvider(ProspectProvider):
    def __init__(self, api_key: str, reveal_email: Optional[bool] = None,
                 reveal_phone: Optional[bool] = None):
        if not api_key:
            raise ApolloError("No Apollo API key provided for this customer.")
        self.api_key = api_key
        self.reveal_email = (config.APOLLO_REVEAL_EMAIL
                             if reveal_email is None else reveal_email)
        self.reveal_phone = (config.APOLLO_REVEAL_PHONE
                             if reveal_phone is None else reveal_phone)

    def find(self, spec, limit: int, exclude_keys: set) -> List[Prospect]:
        candidates = self._search_candidates(spec, exclude_keys,
                                             want=limit * 3)
        if not self.reveal_email:
            # Preview-only mode (no credits spent): return what search gave us.
            return [p for _id, p in candidates][:limit]

        out: List[Prospect] = []
        for pid, p in candidates:
            if len(out) >= limit:
                break
            if not self._enrich(pid, p):
                continue
            # Full keyword exclusion now that we have the description.
            if any(self._excluded(p, kw) for kw in spec.exclude_keywords):
                continue
            if not p.email:            # no usable email after enrichment
                continue
            out.append(p)
            time.sleep(0.3)            # be gentle with the rate limit
        return out

    def _search_candidates(self, spec, exclude_keys: set, want: int):
        """Return [(person_id, Prospect)] previews worth enriching."""
        candidates = []
        company_counts = {}                         # cap prospects per company
        cap = max(1, config.MAX_PER_COMPANY)
        page = 1
        while len(candidates) < want and page <= 25:
            params = spec_to_apollo_params(spec, page=page, per_page=100)
            resp = _post("/mixed_people/api_search", params, self.api_key)
            people = resp.get("people") or []
            if not people:
                break
            for sp in people:
                if not sp.get("has_email"):        # would waste an enrich credit
                    continue
                p = _preview_to_prospect(sp)
                if p.is_duplicate(exclude_keys):    # already delivered (by id)
                    continue
                ckey = (p.company or "").strip().lower()
                if ckey and company_counts.get(ckey, 0) >= cap:
                    continue                        # already have enough here
                # cheap excludes we can do pre-enrichment (title / company name)
                if any(self._excluded(p, kw, desc=False)
                       for kw in spec.exclude_keywords):
                    continue
                candidates.append((sp["id"], p))
                if ckey:
                    company_counts[ckey] = company_counts.get(ckey, 0) + 1
                if len(candidates) >= want:
                    break
            page += 1
            time.sleep(0.3)
        return candidates

    @staticmethod
    def _excluded(p: Prospect, keyword: str, desc: bool = True) -> bool:
        kw = keyword.lower().strip()
        if not kw:
            return False
        blob = f"{p.company} {p.title} {p.industry}"
        if desc:
            blob += f" {p.company_description}"
        return kw in blob.lower()

    def _enrich(self, person_id: str, p: Prospect) -> bool:
        body = {"id": person_id, "reveal_personal_emails": self.reveal_email}
        if self.reveal_phone and config.APOLLO_PHONE_WEBHOOK_URL:
            body["reveal_phone_number"] = True
            body["webhook_url"] = config.APOLLO_PHONE_WEBHOOK_URL
        try:
            resp = _post("/people/match", body, self.api_key)
        except ApolloError:
            return False
        person = resp.get("person") or {}
        if not person:
            return False
        _apply_enrichment(p, person)
        return True


def validate_key(api_key: str) -> bool:
    """Return True if the Apollo key works (a cheap 1-result search)."""
    if not api_key:
        return False
    try:
        _post("/mixed_people/api_search",
              {"person_titles": ["CEO"], "page": 1, "per_page": 1}, api_key)
        return True
    except ApolloError:
        return False


def get_provider(api_key: Optional[str] = None) -> ProspectProvider:
    """Factory. In apollo mode, uses the given per-customer key (falling back to
    the global key only for operator CLI/testing). Otherwise the mock provider."""
    if config.DATA_PROVIDER.lower() == "apollo":
        key = api_key or config.APOLLO_API_KEY
        if key:
            return ApolloProvider(key)
    from .mock import MockProvider
    return MockProvider()
