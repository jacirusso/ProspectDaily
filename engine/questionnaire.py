"""The onboarding questionnaire that captures a customer's B2B target audience.

Answers are stored per-customer and fed to segments.py, which translates them
into concrete prospect-search parameters. Keep questions plain-English so the
web form can render them directly from this schema.
"""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Question:
    key: str
    prompt: str
    help: str = ""
    kind: str = "text"          # text | textarea | multiselect | select | number
    options: List[str] = field(default_factory=list)
    required: bool = True


# NOTE: option lists mirror Apollo's taxonomy where it matters (employee-size
# bands, seniority levels) so the mapping in segments.py stays lossless.
QUESTIONS: List[Question] = [
    Question("company_name", "What is your company's name?"),
    Question("company_offer",
             "In one or two sentences, what do you sell and what problem does it solve?",
             kind="textarea"),
    Question("value_prop",
             "What is the #1 outcome customers get from you?",
             help="Used to write the 'why it's a fit' note and the intro email.",
             kind="textarea"),
    Question("target_industries",
             "Which industries are your best-fit customers in?",
             help="Pick all that apply. Leave blank for 'any'.",
             kind="multiselect", required=False,
             options=[
                 "Software / SaaS", "Marketing & Advertising", "Financial Services",
                 "Healthcare", "Manufacturing", "Real Estate", "Construction",
                 "Retail / E-commerce", "Professional Services", "Education",
                 "Hospitality", "Nonprofit", "Legal", "Logistics & Supply Chain",
             ]),
    Question("target_titles",
             "What job titles are your buyers or decision-makers?",
             help="e.g. 'VP of Marketing, CMO, Head of Growth'. Comma-separated.",
             kind="textarea"),
    Question("target_seniority",
             "What seniority levels should we target?",
             kind="multiselect", required=False,
             options=["Owner", "Founder", "C-Suite", "Partner", "VP",
                      "Head / Director", "Manager", "Senior", "Entry"]),
    Question("company_size",
             "How big are your ideal customer companies (employees)?",
             kind="multiselect", required=False,
             options=["1-10", "11-50", "51-200", "201-500",
                      "501-1000", "1001-5000", "5001-10000", "10001+"]),
    Question("locations",
             "Which locations/regions should we focus on?",
             help="e.g. 'United States, Canada' or 'California, Texas'. Comma-separated. Blank = anywhere.",
             kind="textarea", required=False),
    Question("keywords",
             "Any keywords that describe a great-fit company?",
             help="e.g. 'B2B, franchise, multi-location, series A'. Comma-separated.",
             kind="textarea", required=False),
    Question("exclude_keywords",
             "Anything that should DISQUALIFY a prospect?",
             help="e.g. 'staffing agency, competitor, government'. Comma-separated.",
             kind="textarea", required=False),
    Question("groups_per_day",
             "How many prospects per day? (in groups of 10)",
             help="How many prospects per day should we deliver?",
             kind="select", options=["10", "20", "30", "40", "50", "100"]),
    Question("sender_name", "Whose name should the intro emails be signed with?"),
    Question("sender_title", "What's that person's title?", required=False),
    Question("sender_email",
             "What email address should sign the intro emails / receive replies?",
             help="Appears in the signature so prospects can reply to a real inbox."),
    Question("sender_phone", "Phone number for the email signature?",
             required=False),
    Question("company_website", "Your company website (for the signature)?",
             help="e.g. https://brandstateu.com", required=False),
    Question("booking_link",
             "Your calendar / booking link (Calendly, etc.)?",
             help="If provided, each intro email invites the prospect to book a call directly.",
             required=False),
    Question("email_tone",
             "What tone should the intro emails have?",
             kind="select",
             options=["Warm & consultative", "Direct & concise",
                      "Friendly & casual", "Formal & professional"]),
]


def blank_answers() -> dict:
    return {q.key: ("" if q.kind != "multiselect" else []) for q in QUESTIONS}


def validate(answers: dict) -> List[str]:
    """Return a list of human-readable problems; empty list means valid."""
    problems = []
    for q in QUESTIONS:
        if not q.required:
            continue
        val = answers.get(q.key)
        if val is None or (isinstance(val, str) and not val.strip()) or \
           (isinstance(val, list) and not val):
            problems.append(f"Missing answer: {q.prompt}")
    return problems
