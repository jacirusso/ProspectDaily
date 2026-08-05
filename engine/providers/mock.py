"""Deterministic mock provider. Generates realistic-looking (but fictional)
prospects so the full pipeline can be run and tested with no API keys.

Determinism is seeded by the SearchSpec so re-runs are stable, but each call
advances an internal cursor so consecutive requests return NEW people --
exactly the behavior the dedupe layer needs to be exercised against.
"""
from typing import List
import hashlib
from .base import Prospect, ProspectProvider

_FIRST = ["Sarah", "James", "Maria", "David", "Priya", "Michael", "Emily",
          "Carlos", "Aisha", "John", "Linda", "Wei", "Robert", "Fatima",
          "Daniel", "Sofia", "Kevin", "Nina", "Thomas", "Grace"]
_LAST = ["Chen", "Patel", "Johnson", "Garcia", "Kim", "Nguyen", "Smith",
         "Okafor", "Rossi", "Muller", "Silva", "Cohen", "Ahmed", "Brown",
         "Ivanov", "Tanaka", "Lopez", "Weber", "Dubois", "Novak"]
_COMPANY_ROOT = ["Northwind", "Brightpath", "Summit", "Vertex", "Cedar",
                 "Ironclad", "Bluewave", "Kingfisher", "Redwood", "Anchor",
                 "Lighthouse", "Meridian", "Copperline", "Trailhead", "Beacon"]
_COMPANY_SUFFIX = ["Solutions", "Group", "Partners", "Labs", "Industries",
                   "Systems", "Collective", "Works", "Digital", "Holdings"]
_CITIES = [("Austin", "TX"), ("Denver", "CO"), ("Chicago", "IL"),
           ("Atlanta", "GA"), ("Seattle", "WA"), ("Boston", "MA"),
           ("Miami", "FL"), ("Portland", "OR"), ("Nashville", "TN"),
           ("Phoenix", "AZ")]


def _pick(lst, label, n):
    """Independent per-field selection: hashing (label,n) avoids the arithmetic
    aliasing that made different indices collide onto identical people."""
    h = int(hashlib.sha1(f"{label}:{n}".encode()).hexdigest(), 16)
    return lst[h % len(lst)]


class MockProvider(ProspectProvider):
    def find(self, spec, limit: int, exclude_keys: set) -> List[Prospect]:
        seed_basis = "|".join(spec.titles + spec.industries + spec.keywords) or "generic"
        base = int(hashlib.sha1(seed_basis.encode()).hexdigest(), 16)
        title = spec.titles[0] if spec.titles else "Director of Operations"
        industry = spec.industries[0] if spec.industries else "Professional Services"

        out: List[Prospect] = []
        i = 0
        # Keep pulling until we have `limit` NON-excluded prospects (or give up).
        while len(out) < limit and i < limit * 50:
            n = base + i
            i += 1
            fn = _pick(_FIRST, "fn", n)
            ln = _pick(_LAST, "ln", n)
            root = _pick(_COMPANY_ROOT, "root", n)
            suffix = _pick(_COMPANY_SUFFIX, "suffix", n)
            company = f"{root} {suffix}"
            city, state = _pick(_CITIES, "city", n)
            # Company id keeps domains/emails unique even if name pools repeat.
            cid = hashlib.sha1(f"co:{n}".encode()).hexdigest()[:4]
            domain = f"{root.lower()}{suffix.lower().replace(' ', '')}-{cid}.com"
            p = Prospect(
                source="mock",
                source_id=f"mock-{hashlib.sha1(str(n).encode()).hexdigest()[:12]}",
                first_name=fn, last_name=ln,
                title=spec.titles[i % len(spec.titles)] if spec.titles else title,
                email=f"{fn.lower()}.{ln.lower()}@{domain}",
                phone=f"+1 ({200 + n % 700}) {100 + n % 900:03d}-{1000 + n % 9000:04d}",
                linkedin_url=f"https://www.linkedin.com/in/{fn.lower()}-{ln.lower()}-{n % 9999}",
                company=company,
                website=f"https://{domain}",
                company_address=f"{100 + n % 9800} {root} Ave, {city}, {state}",
                company_description=(
                    f"{company} is a {industry.lower()} company serving mid-market "
                    f"clients across North America, known for {root.lower()}-grade "
                    f"reliability and a consultative approach."),
                industry=industry,
                employee_count=[15, 45, 120, 350, 800, 2500][n % 6],
            )
            if p.is_duplicate(exclude_keys):
                continue
            out.append(p)
        return out
