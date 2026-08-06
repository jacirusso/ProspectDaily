"""Daily scheduler entrypoint. Runs the pipeline for every ACTIVE customer and
logs each run (success OR failure) so problems are visible on the dashboard.
On Render, a Cron Job runs this once every morning:

    python -m engine.daily

Safe to run more than once a day: the dedupe ledger guarantees no customer ever
receives a repeated prospect, and re-running simply tops up with fresh ones.
"""
import logging
import os
from datetime import date

from engine import config
from engine.pipeline import run_for_customer
from web import store

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("daily")


def run_all(run_date: str = None) -> dict:
    run_date = run_date or date.today().isoformat()
    customers = store.active_customers()
    log.info("%s: %d active customer(s)", run_date, len(customers))
    ok, failed = 0, 0
    failures = []
    for c in customers:
        try:
            result = run_for_customer(
                c["id"], c["answers"],
                folder_id=c.get("folder_id", ""),
                ordered=c.get("ordered_per_day", 10),
                apollo_key=c.get("apollo_api_key", ""),
                run_date=run_date)
            store.log_run(c["id"], run_date, result.ordered, result.delivered,
                          result.csv_path, result.sheet_url)
            if result.folder_url:
                store.update_customer(c["id"], client_folder_url=result.folder_url)
            if result.delivered:
                try:
                    from web import emails
                    emails.send_first_report(store.get_customer(c["id"]),
                                             result.folder_url or "")
                except Exception:
                    log.exception("first-report email failed for %s", c["email"])
            ok += 1
            log.info("  ✓ %s: delivered %d (sheet: %s)", c["email"],
                     result.delivered, result.sheet_url or "CSV only")
        except Exception as e:
            failed += 1
            failures.append((c["email"], str(e)[:200]))
            log.exception("  ✗ %s failed", c["email"])
            store.log_run(c["id"], run_date, c.get("ordered_per_day", 10), 0,
                          None, None, status="error", error=str(e)[:500])

    from web import emails
    # Lifecycle emails (day-1 nudges, day-4 tips) for the whole customer base.
    try:
        sent = emails.run_lifecycle()
        log.info("lifecycle emails sent: %d", sent)
    except Exception:
        log.exception("lifecycle email sweep failed")

    # Alert the operator if any runs failed.
    if failures:
        try:
            rows = "".join(f"<li>{e} — {err}</li>" for e, err in failures)
            emails.alert_operator(
                f"{failed} failed delivery run(s) on {run_date}",
                f"<p>These customers' reports failed today:</p><ul>{rows}</ul>"
                f"<p>Check the Render logs for details.</p>")
        except Exception:
            log.exception("operator failure alert failed")

    # Estimate Apollo credit usage this month and alert if over budget.
    try:
        import datetime
        y, m = run_date.split("-")[0], run_date.split("-")[1]
        month_start = int(datetime.datetime(int(y), int(m), 1,
                          tzinfo=datetime.timezone.utc).timestamp())
        used = store.prospects_delivered_since(month_start)
        budget = config.APOLLO_MONTHLY_CREDITS
        if budget and used >= budget * config.APOLLO_CREDIT_ALERT_PCT:
            emails.alert_operator(
                f"Apollo credits at {used}/{budget} this month",
                f"<p>You've used about <strong>{used}</strong> of your ~{budget} "
                f"monthly Apollo credit budget ({used*100//budget}%). Consider "
                f"upgrading your Apollo plan or pausing test accounts before "
                f"deliveries start failing.</p>")
    except Exception:
        log.exception("credit-usage alert failed")

    summary = {"date": run_date, "customers": len(customers),
               "ok": ok, "failed": failed}
    log.info("done: %s", summary)
    return summary


if __name__ == "__main__":
    run_all()
