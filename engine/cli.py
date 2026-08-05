"""Command-line runner for the daily pipeline. Useful for testing the engine
without the web app.

Examples:
    # Run the built-in demo customer with mock data:
    python3 -m engine.cli --demo

    # Run for a customer whose answers live in a JSON file, deliver to Drive:
    python3 -m engine.cli --answers ./cust.json --customer acme --folder <DRIVE_FOLDER_ID>
"""
import argparse
import json
import sys

from . import config
from .pipeline import run_for_customer


DEMO_ANSWERS = {
    "company_name": "BrandState U",
    "company_offer": "We run done-for-you brand and demand-gen programs for B2B companies.",
    "value_prop": "a predictable pipeline of qualified sales conversations",
    "target_industries": ["Software / SaaS", "Professional Services"],
    "target_titles": "VP of Marketing, CMO, Head of Growth, Director of Demand Generation",
    "target_seniority": ["VP", "C-Suite", "Head / Director"],
    "company_size": ["51-200", "201-500"],
    "locations": "United States",
    "keywords": "B2B, series A, series B",
    "exclude_keywords": "staffing agency, marketing agency",
    "groups_per_day": "10",
    "sender_name": "Jaci Russo",
    "sender_title": "CEO",
    "sender_email": "jaci@brandstateu.com",
    "sender_phone": "+1 (337) 555-0142",
    "company_website": "https://brandstateu.com",
    "booking_link": "https://calendly.com/brandstateu/intro",
    "email_tone": "Warm & consultative",
}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run the daily prospect pipeline.")
    ap.add_argument("--demo", action="store_true",
                    help="Run the built-in demo customer with mock data.")
    ap.add_argument("--answers", help="Path to a JSON file of questionnaire answers.")
    ap.add_argument("--customer", default="demo", help="Customer id (dedupe scope).")
    ap.add_argument("--folder", default="", help="Google Drive folder id to deliver into.")
    ap.add_argument("--date", default=None, help="Override run date (YYYY-MM-DD).")
    args = ap.parse_args(argv)

    if args.demo:
        answers, customer = DEMO_ANSWERS, args.customer or "demo"
    elif args.answers:
        with open(args.answers) as f:
            answers = json.load(f)
        customer = args.customer
    else:
        ap.error("Pass --demo or --answers <file>.")
        return 2

    result = run_for_customer(customer, answers, folder_id=args.folder,
                              run_date=args.date)

    print("=" * 70)
    print(f"Customer:            {result.customer_id}")
    print(f"Ordered:             {result.ordered} prospects")
    print(f"Delivered ({config.OVERDELIVER_MULTIPLIER}x):    {result.delivered} prospects")
    print(f"Newly recorded:      {result.newly_recorded} (never sent before)")
    print(f"Total ever delivered:{result.total_ever_delivered}")
    print(f"CSV report:          {result.csv_path}")
    print(f"Google Sheet:        {result.sheet_url or '(Drive not configured — CSV only)'}")
    print("=" * 70)
    for i, p in enumerate(result.prospects[:3], 1):
        print(f"\n--- Sample prospect {i} ---")
        print(f"{p.full_name}, {p.title} @ {p.company}")
        print(f"  {p.email} | {p.phone}")
        print(f"  {p.linkedin_url}")
        print(f"  Fit: {p.fit_reason}")
        print(f"  Email subject: {p.intro_email_subject}")
    if len(result.prospects) > 3:
        print(f"\n... and {len(result.prospects) - 3} more in the CSV.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
