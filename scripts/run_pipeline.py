#!/usr/bin/env python3
"""Run pipeline stages incrementally for integration testing.

Usage:
    pdm run python run_pipeline.py stage1 <url>
    pdm run python run_pipeline.py stage2a <id> --query "75 inch QLED 4K TV"
    pdm run python run_pipeline.py stage2b <id> --match-all
    pdm run python run_pipeline.py stage3
    pdm run python run_pipeline.py stage4
    pdm run python run_pipeline.py stage5
    pdm run python run_pipeline.py stage6
    pdm run python run_pipeline.py status [source_product_id]
"""

import argparse
import logging
import sys

from app.base.config import configure_logging

configure_logging()
log = logging.getLogger("pipeline")


def cmd_stage1(args):
    from app.pipeline.stages.s1_spec_extraction import extract_specs

    product = extract_specs(args.url)
    log.info("Stage 1 complete.")
    log.info("  Source product ID: %d", product.id)
    log.info("  Title: %s", product.title)
    log.info("  Spec groups: %d", len(product.specs))
    log.info(
        "Use this ID for stage2: pdm run python run_pipeline.py stage2 %d", product.id
    )


def cmd_stage2(args):
    from app.pipeline.stages.s2_supplier_search import run_supplier_search

    threads = run_supplier_search(args.source_product_id)
    log.info("Stage 2 complete. %d supplier threads created.", len(threads))
    for t in threads:
        log.info(
            "  Thread %d: supplier_product=%d state=%s",
            t.id,
            t.supplier_product_id,
            t.state,
        )
    if threads:
        log.info("Run stage3: pdm run python run_pipeline.py stage3")


def cmd_stage2a(args):
    from app.pipeline.stages.s2_supplier_search import search_and_extract

    queries = args.query if args.query else None
    products = search_and_extract(args.source_product_id, queries=queries)
    log.info("Stage 2a complete. %d supplier products extracted.", len(products))
    for sp in products:
        log.info("  %s — %s", sp.product_url[:80], sp.title[:60])
    log.info(
        "Run matching: pdm run python run_pipeline.py stage2b %d",
        args.source_product_id,
    )


def cmd_stage2b(args):
    from app.pipeline.stages.s2_supplier_search import match_candidates

    threads = match_candidates(args.source_product_id, match_all=args.match_all)
    log.info("Stage 2b complete. %d threads matched.", len(threads))
    for t in threads:
        log.info(
            "  Thread %d: supplier_product=%d state=%s",
            t.id,
            t.supplier_product_id,
            t.state,
        )


def cmd_stage3(args):
    from app.pipeline.stages.s3_outreach import send_outreach

    count = send_outreach(agent_only=args.agent)
    log.info("Stage 3 complete. %d inquiries sent.", count)


def cmd_stage4(args):
    from app.pipeline.stages.s4_inbox_triage import triage_inbox

    counts = triage_inbox()
    log.info("Stage 4 complete. %s", counts)


def cmd_stage5(args):
    from app.pipeline.stages.s5_negotiation import process_negotiations

    counts = process_negotiations()
    log.info("Stage 5 complete. %s", counts)


def cmd_stage6(args):
    from app.pipeline.stages.s6_sheet_update import update_sheet

    count = update_sheet()
    log.info("Stage 6 complete. %d rows upserted.", count)


def cmd_status(args):
    from app.db.database import SessionLocal
    from app.db.models.source_product import SourceProduct
    from app.db.models.supplier_product import SupplierProduct
    from app.db.models.supplier_thread import SupplierThread

    with SessionLocal() as session:
        if args.source_product_id:
            sp = session.get(SourceProduct, args.source_product_id)
            if not sp:
                log.error("Source product %d not found", args.source_product_id)
                return
            products = [sp]
        else:
            products = session.query(SourceProduct).all()

        if not products:
            log.info("No source products in database.")
            return

        for sp in products:
            sup_products = (
                session.query(SupplierProduct)
                .filter_by(source_product_id=sp.id)
                .count()
            )
            threads = (
                session.query(SupplierThread).filter_by(source_product_id=sp.id).all()
            )
            state_counts = {}
            for t in threads:
                state_counts[t.state] = state_counts.get(t.state, 0) + 1

            log.info("Source product %d: %s", sp.id, sp.title[:60])
            log.info("  URL: %s", sp.url)
            log.info("  Supplier products: %d", sup_products)
            log.info(
                "  Threads: %d %s",
                len(threads),
                dict(state_counts) if state_counts else "",
            )


def main():
    parser = argparse.ArgumentParser(description="Run pipeline stages incrementally")
    sub = parser.add_subparsers(dest="command", required=True)

    s1 = sub.add_parser("stage1", help="Extract specs from a retailer URL")
    s1.add_argument("url", help="Retailer product URL")
    s1.set_defaults(func=cmd_stage1)

    s2 = sub.add_parser("stage2", help="Full supplier search + match")
    s2.add_argument("source_product_id", type=int)
    s2.set_defaults(func=cmd_stage2)

    s2a = sub.add_parser("stage2a", help="Search & extract only (no LLM matching)")
    s2a.add_argument("source_product_id", type=int)
    s2a.add_argument(
        "--query", nargs="+", help="Search queries (skip LLM query generation)"
    )
    s2a.set_defaults(func=cmd_stage2a)

    s2b = sub.add_parser("stage2b", help="Match candidates only (LLM)")
    s2b.add_argument("source_product_id", type=int)
    s2b.add_argument(
        "--match-all",
        action="store_true",
        help="Accept all candidates (skip LLM matching)",
    )
    s2b.set_defaults(func=cmd_stage2b)

    s3 = sub.add_parser("stage3", help="Send outreach for all NEW threads")
    s3.add_argument(
        "--agent",
        action="store_true",
        help="Skip deterministic flow, go direct to LLM agent",
    )
    s3.set_defaults(func=cmd_stage3)

    s4 = sub.add_parser("stage4", help="Triage Gmail inbox")
    s4.set_defaults(func=cmd_stage4)

    s5 = sub.add_parser("stage5", help="Process negotiations")
    s5.set_defaults(func=cmd_stage5)

    s6 = sub.add_parser("stage6", help="Update output Google Sheet")
    s6.set_defaults(func=cmd_stage6)

    st = sub.add_parser("status", help="Show pipeline status")
    st.add_argument("source_product_id", type=int, nargs="?", default=None)
    st.set_defaults(func=cmd_status)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
