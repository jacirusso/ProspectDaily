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
    for c in customers:
        try:
            result = run_for_customer(
                c["id"], c["answers"],
                folder_id=c.get("folder_id", ""),
                ordered=c.get("ordered_per_day", 10),
                run_date=run_date)
            store.log_run(c["id"], run_date, result.ordered, result.delivered,
                          result.csv_path, result.sheet_url)
            if result.folder_url:
                store.update_customer(c["id"], client_folder_url=result.folder_url)
            ok += 1
            log.info("  ✓ %s: delivered %d (sheet: %s)", c["email"],
                     result.delivered, result.sheet_url or "CSV only")
        except Exception as e:
            failed += 1
            log.exception("  ✗ %s failed", c["email"])
            store.log_run(c["id"], run_date, c.get("ordered_per_day", 10), 0,
                          None, None, status="error", error=str(e)[:500])
    summary = {"date": run_date, "customers": len(customers),
               "ok": ok, "failed": failed}
    log.info("done: %s", summary)
    return summary


if __name__ == "__main__":
    run_all()
