"""The daily pipeline. For one customer:

  1. Read their answers -> SearchSpec.
  2. Compute delivered = ordered + bonus + spares (config.delivered_for).
  3. Ask the provider for `delivered` NEW prospects, excluding everyone this
     customer has ever received (the dedupe ledger).
  4. Generate fit rationale + intro email for each.
  5. Record them in the ledger (so they never repeat) and build the report.
  6. Deliver to Google Drive if configured; always write a local CSV too.
"""
import os
from dataclasses import dataclass
from datetime import date
from typing import List, Optional

from . import config, dedupe, ai, google_drive
from .report import write_csv, pdf_html
from .segments import spec_from_answers
from .providers import get_provider
from .providers.base import Prospect


@dataclass
class RunResult:
    customer_id: str
    ordered: int
    delivered: int
    prospects: List[Prospect]
    csv_path: str
    sheet_url: Optional[str]
    newly_recorded: int
    total_ever_delivered: int
    folder_url: Optional[str] = None   # the client's own Drive folder (link-shared)


def run_for_customer(customer_id: str, answers: dict,
                     folder_id: str = "",
                     run_date: Optional[str] = None,
                     ordered: Optional[int] = None,
                     apollo_key: str = "",   # deprecated: operator key is used now
                     provider=None) -> RunResult:
    run_date = run_date or date.today().isoformat()
    # `ordered` (from the customer's plan) wins; else fall back to the answer.
    if ordered is None:
        ordered = int(str(answers.get("groups_per_day", "10")).split()[0] or 10)
    target = config.delivered_for(ordered)

    spec = spec_from_answers(answers)
    already = dedupe.seen_keys(customer_id)
    if provider is None:
        # We hold one redistribution-licensed data account (operator key from
        # env); customers bring nothing. `apollo_key` is an optional override.
        provider = get_provider(api_key=apollo_key)

    # 1. Find fresh prospects, excluding everyone already delivered.
    prospects = provider.find(spec, limit=target, exclude_keys=already)

    # 2. Safety net: hard-dedupe within this batch and against the ledger,
    #    matching on EVERY identity signal (id, email, linkedin, name+company),
    #    and cap prospects-per-company for this batch.
    seen_now = set(already)
    company_counts = {}
    cap = max(1, config.MAX_PER_COMPANY)
    unique: List[Prospect] = []
    for p in prospects:
        if p.is_duplicate(seen_now):
            continue
        ckey = (p.company or "").strip().lower()
        if ckey and company_counts.get(ckey, 0) >= cap:
            continue
        seen_now.update(p.identity_keys())
        if ckey:
            company_counts[ckey] = company_counts.get(ckey, 0) + 1
        unique.append(p)
    prospects = unique[:target]

    # Tag each prospect's report section: the ordered count is "core", then a
    # bonus block, then the spare swap-ins.
    split = config.delivery_split(ordered)
    for i, p in enumerate(prospects):
        if i < split["core"]:
            p.tier = "core"
        elif i < split["core"] + split["bonus"]:
            p.tier = "bonus"
        else:
            p.tier = "spare"

    # 3. AI copywriting for each kept prospect, in the customer's own voice.
    try:
        from . import voice as voice_mod
        answers = dict(answers)
        prof = voice_mod.profile_for(customer_id)
        answers["voice_sample"] = prof["sample"]
        answers["voice_notes"] = prof["notes"]
        answers["_voice_examples"] = voice_mod.examples_for(customer_id, limit=3)
    except Exception as e:
        print(f"[warn] voice enrichment failed: {e}")
    for p in prospects:
        ai.write_for(p, answers)

    # 4. Record in the ledger BEFORE reporting, so a crash can't double-send.
    newly = dedupe.record(customer_id, prospects)
    # Capture the full prospect payloads for the LeadDaily CRM add-on. Never let
    # a CRM-storage hiccup break the actual report delivery.
    try:
        dedupe.record_report_prospects(customer_id, run_date, prospects)
    except Exception as e:
        print(f"[warn] LeadDaily prospect capture failed: {e}")

    # 5. Build the report.
    out_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                           "data", "reports", customer_id)
    csv_path = write_csv(
        prospects, os.path.join(out_dir, f"prospects-{run_date}.csv"))

    # 6. Deliver to Google Drive if a backend is available (OAuth = the user's
    #    own Drive, no folder id needed; service account = the given folder).
    #    Format is a styled Google Doc by default, or a Sheet if configured.
    sheet_url = None
    folder_url = None
    # Each client gets their OWN Drive folder (named after their company),
    # shared by private link — everything stays in one (operator) Drive.
    client_label = (answers.get("company_name") or customer_id)[:120].strip()
    if google_drive.is_available():
        title = f"{answers.get('company_name','Prospects')} — Daily Prospects {run_date}"
        fmt = (config.REPORT_FORMAT or "pdf").lower()
        try:
            if fmt == "pdf":
                from . import pdf as pdf_mod
                try:
                    if not pdf_mod.available():
                        raise RuntimeError("WeasyPrint not installed")
                    html = pdf_html(prospects, answers.get("company_name", ""), run_date)
                    sheet_url = google_drive.deliver_pdf(
                        pdf_mod.render_pdf(html), folder_id, title,
                        client_label=client_label)
                except Exception as pe:  # PDF render/deps issue -> Doc fallback
                    print(f"[warn] PDF render failed ({pe}); falling back to Google Doc")
                    sheet_url = google_drive.deliver_doc(prospects, folder_id, title,
                                                         client_label=client_label)
            elif fmt == "sheet":
                sheet_url = google_drive.deliver(prospects, folder_id, title,
                                                 client_label=client_label)
            else:
                sheet_url = google_drive.deliver_doc(prospects, folder_id, title,
                                                     client_label=client_label)
            folder_url = google_drive.client_folder_link(client_label)
        except Exception as e:  # never lose the run over a delivery hiccup
            print(f"[warn] Google Drive delivery failed: {e}")

    return RunResult(
        customer_id=customer_id, ordered=ordered, delivered=len(prospects),
        prospects=prospects, csv_path=csv_path, sheet_url=sheet_url,
        newly_recorded=newly,
        total_ever_delivered=dedupe.total_delivered(customer_id),
        folder_url=folder_url,
    )
