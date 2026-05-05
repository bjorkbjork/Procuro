"""Scheduler entry point — two pipeline loops on cron schedules.

Sourcing pipeline (default every 15 min):
    Trigger (poll input sheet) → stage 1 (spec extraction) → stage 2 (supplier search)
    → stage 3 (outreach) → stage 6 (sheet sync)

Negotiation pipeline (default every 30 min):
    Stage 4 (inbox triage) → stage 5 (negotiation) → stage 6 (sheet sync)

Run directly: pdm run python -m app.main
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from app.base.config import scheduler_settings, settings
from app.base.scheduler import scheduler

log = logging.getLogger(__name__)


def _run_stage(stage_name: str, func, *args, **kwargs):
    log.info("Stage %s: starting", stage_name)
    t0 = time.monotonic()
    try:
        result = func(*args, **kwargs)
        elapsed = time.monotonic() - t0
        log.info("Stage %s: finished in %.1fs — %s", stage_name, elapsed, result)
        return result
    except Exception:
        elapsed = time.monotonic() - t0
        log.exception("Stage %s: failed after %.1fs", stage_name, elapsed)
        return None


def _fan_out(stage_name: str, func, items, *, max_workers=None, label=None):
    """Run func(item) for each item in parallel, barrier at the end.

    Args:
        label: optional callable (item) -> str for thread names and log messages.

    Returns list of (item, result) tuples. Failed items get (item, None).
    """
    if not items:
        return []

    max_workers = max_workers or settings.MAX_WORKERS
    label = label or str
    log.info(
        "Stage %s: starting (%d items, %d workers)", stage_name, len(items), max_workers
    )
    t0 = time.monotonic()
    results = []

    def _run(item):
        threading.current_thread().name = f"{stage_name}/{label(item)}"
        return func(item)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_item = {pool.submit(_run, item): item for item in items}
        for future in as_completed(future_to_item):
            item = future_to_item[future]
            try:
                result = future.result()
                results.append((item, result))
            except Exception:
                log.exception("Stage %s: failed for %s", stage_name, label(item))
                results.append((item, None))

    succeeded = sum(1 for _, r in results if r is not None)
    elapsed = time.monotonic() - t0
    log.info(
        "Stage %s: finished in %.1fs — %d/%d succeeded",
        stage_name,
        elapsed,
        succeeded,
        len(items),
    )
    return results


def sourcing_pipeline():
    from app.pipeline.triggers.input_sheet import get_new_urls
    from app.pipeline.stages.s1_spec_extraction import extract_specs
    from app.pipeline.stages.s2_supplier_search import run_supplier_search
    from app.pipeline.stages.s3_outreach import send_outreach
    from app.pipeline.stages.s6_sheet_update import update_sheet
    from app.services.sheets import SheetsService

    pending = _run_stage("trigger_input_sheet", get_new_urls)
    if not pending:
        log.info("Sourcing pipeline: no new URLs")
        return

    sheets = SheetsService()
    for item in pending:
        sheets.update_input_status(item["row_index"], "processing")

    # Stage 1: extract specs — fan out across URLs
    def _slug_from_url(url):
        return url.rstrip("/").rsplit("/", 1)[-1]

    url_to_item = {item["url"]: item for item in pending}
    s1_results = _fan_out(
        "1_spec_extraction",
        extract_specs,
        [i["url"] for i in pending],
        label=_slug_from_url,
    )

    products = []
    for url, result in s1_results:
        item = url_to_item[url]
        if result is not None:
            sheets.update_input_status(item["row_index"], "done")
            products.append(result)
        else:
            sheets.update_input_status(item["row_index"], "error")

    if not products:
        return

    # Stage 2: supplier search — fan out across products
    slug_by_id = {p.id: p.slug for p in products}
    _fan_out(
        "2_supplier_search",
        run_supplier_search,
        [p.id for p in products],
        label=lambda pid: slug_by_id[pid],
    )

    # Stage 3: outreach (has its own internal threading per platform group)
    _run_stage("3_outreach", send_outreach)
    _run_stage("6_sheet_update", update_sheet)


def recover_stalled():
    """Detect orphaned or stalled work across all pipeline stages and recover.

    Checks (in pipeline order):
    1. supplier_products with match_status='pending' → re-run matching (stage 2)
    2. source_products under match threshold → re-run full search loop (stage 2)
    3. supplier_threads stuck in NEW → re-run outreach (stage 3)
    """
    from datetime import datetime, timezone, timedelta

    from app.db.database import SessionLocal
    from app.db.models.supplier_product import SupplierProduct
    from app.db.models.supplier_thread import SupplierThread
    from app.pipeline.stages.s2_supplier_search import (
        match_candidates,
        run_supplier_search,
    )
    from app.pipeline.stages.s3_outreach import send_outreach

    # --- Stage 2a: unmatched supplier products ----------------------------------
    with SessionLocal() as session:
        pending_sources = (
            session.query(SupplierProduct.source_product_id)
            .filter(SupplierProduct.match_status == "pending")
            .distinct()
            .all()
        )
    pending_sids = set(row[0] for row in pending_sources)

    if pending_sids:
        log.info("Recovery: %d source products with pending matches", len(pending_sids))
        _fan_out(
            "recovery_match",
            lambda sid: match_candidates(sid, only_pending=True),
            list(pending_sids),
            label=str,
        )

    # --- Stage 2b: under-matched source products --------------------------------
    with SessionLocal() as session:
        all_searched_sids = {
            row[0]
            for row in session.query(SupplierProduct.source_product_id).distinct().all()
        } - pending_sids

        under_matched = []
        for sid in all_searched_sids:
            thread_count = (
                session.query(SupplierThread).filter_by(source_product_id=sid).count()
            )
            if thread_count >= settings.MIN_MATCHES_PER_PRODUCT:
                continue
            cand_count = (
                session.query(SupplierProduct).filter_by(source_product_id=sid).count()
            )
            if cand_count >= settings.MAX_CANDIDATES_PER_PRODUCT:
                continue
            under_matched.append(sid)

    if under_matched:
        log.info(
            "Recovery: %d source products under %d-match threshold",
            len(under_matched),
            settings.MIN_MATCHES_PER_PRODUCT,
        )
        _fan_out(
            "recovery_search",
            run_supplier_search,
            under_matched,
            max_workers=1,
            label=str,
        )

    # --- Stage 3: threads stuck in NEW ------------------------------------------
    cutoff = datetime.now(timezone.utc) - timedelta(
        minutes=scheduler_settings.STALLED_OUTREACH_MINUTES
    )
    with SessionLocal() as session:
        stalled = (
            session.query(SupplierThread)
            .filter(
                SupplierThread.state == "NEW",
                SupplierThread.created_at < cutoff,
            )
            .count()
        )

    if stalled:
        log.info("Recovery: %d threads stalled in NEW", stalled)
        _run_stage("recovery_outreach", send_outreach)


def negotiation_pipeline():
    from app.pipeline.stages.s4_inbox_triage import triage_inbox
    from app.pipeline.stages.s5_negotiation import process_negotiations
    from app.pipeline.stages.s6_sheet_update import update_sheet

    counts = _run_stage("4_inbox_triage", triage_inbox)

    has_replies = counts and counts.get("supplier_reply", 0) > 0
    if has_replies:
        _run_stage("5_negotiation", process_negotiations)
        _run_stage("6_sheet_update", update_sheet)
    else:
        log.info("Negotiation pipeline: no supplier replies, skipping stages 5-6")


def sync_reporting():
    """Sync all reporting tabs and check for anomalies. Cheap deterministic job."""
    from app.pipeline.browser_executor import check_automation_failure_rate
    from app.pipeline.stages.s6_sheet_update import sync_automation_stats

    _run_stage("sync_automation_stats", sync_automation_stats)
    _run_stage("check_failure_rate", check_automation_failure_rate)


def register_jobs():
    sourcing_minutes = scheduler_settings.SOURCING_INTERVAL_MINUTES
    negotiation_minutes = scheduler_settings.NEGOTIATION_INTERVAL_MINUTES

    now = datetime.now()
    scheduler.add_job(
        sourcing_pipeline,
        trigger="cron",
        minute=f"*/{sourcing_minutes}",
        id="sourcing_pipeline",
        replace_existing=True,
        next_run_time=now,
    )
    scheduler.add_job(
        negotiation_pipeline,
        trigger="cron",
        minute=f"*/{negotiation_minutes}",
        hour="7-18",
        day_of_week="mon-fri",
        timezone="Australia/Sydney",
        id="negotiation_pipeline",
        replace_existing=True,
    )
    scheduler.add_job(
        recover_stalled,
        trigger="cron",
        minute=f"*/{sourcing_minutes}",
        id="recover_stalled",
        replace_existing=True,
        next_run_time=now,
    )
    scheduler.add_job(
        sync_reporting,
        trigger="cron",
        minute="*/30",
        id="sync_reporting",
        replace_existing=True,
        next_run_time=now,
    )
    log.info(
        "Registered jobs — sourcing every %d min, negotiation every %d min (cron-aligned)",
        sourcing_minutes,
        negotiation_minutes,
    )


def main():
    from app.base.config import configure_logging

    configure_logging()
    log.info("Starting scheduler")
    register_jobs()
    scheduler.start()


if __name__ == "__main__":
    main()
