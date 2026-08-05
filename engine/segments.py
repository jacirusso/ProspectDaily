"""Translate questionnaire answers into a provider-agnostic SearchSpec, and
from there into Apollo.io API query parameters.

Keeping this separate means we can swap Apollo for another data vendor by
writing one new adapter, without touching the questionnaire or pipeline.
"""
from dataclasses import dataclass, field
from typing import List


# Maps our friendly seniority labels -> Apollo `person_seniorities` values.
_SENIORITY_MAP = {
    "Owner": "owner", "Founder": "founder", "C-Suite": "c_suite",
    "Partner": "partner", "VP": "vp", "Head / Director": "director",
    "Manager": "manager", "Senior": "senior", "Entry": "entry",
}

# Our employee bands are already Apollo-compatible ("min,max" ranges).
_SIZE_MAP = {
    "1-10": "1,10", "11-50": "11,50", "51-200": "51,200",
    "201-500": "201,500", "501-1000": "501,1000", "1001-5000": "1001,5000",
    "5001-10000": "5001,10000", "10001+": "10001,1000000",
}


def _split(csv: str) -> List[str]:
    if not csv:
        return []
    return [p.strip() for p in str(csv).replace("\n", ",").split(",") if p.strip()]


@dataclass
class SearchSpec:
    """Provider-neutral description of who to look for."""
    titles: List[str] = field(default_factory=list)
    seniorities: List[str] = field(default_factory=list)
    industries: List[str] = field(default_factory=list)
    employee_ranges: List[str] = field(default_factory=list)
    locations: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    exclude_keywords: List[str] = field(default_factory=list)


def spec_from_answers(answers: dict) -> SearchSpec:
    return SearchSpec(
        titles=_split(answers.get("target_titles", "")),
        seniorities=[_SENIORITY_MAP[s] for s in answers.get("target_seniority", [])
                     if s in _SENIORITY_MAP],
        industries=list(answers.get("target_industries", [])),
        employee_ranges=[_SIZE_MAP[s] for s in answers.get("company_size", [])
                         if s in _SIZE_MAP],
        locations=_split(answers.get("locations", "")),
        keywords=_split(answers.get("keywords", "")),
        exclude_keywords=_split(answers.get("exclude_keywords", "")),
    )


def spec_to_apollo_params(spec: SearchSpec, page: int, per_page: int) -> dict:
    """Build the JSON body for Apollo's people search endpoint."""
    params: dict = {"page": page, "per_page": per_page}
    if spec.titles:
        params["person_titles"] = spec.titles
    if spec.seniorities:
        params["person_seniorities"] = spec.seniorities
    if spec.locations:
        params["person_locations"] = spec.locations
    if spec.employee_ranges:
        params["organization_num_employees_ranges"] = spec.employee_ranges
    # Apollo treats industry/keyword matching through organization keyword tags.
    org_keywords = list(spec.industries) + list(spec.keywords)
    if org_keywords:
        params["q_organization_keyword_tags"] = org_keywords
    return params
