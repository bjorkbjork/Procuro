#!/usr/bin/env python3
"""Run pipeline stages incrementally for integration testing.

Usage:
    pdm run python run_pipeline.py stage1 <url>
    pdm run python run_pipeline.py stage2a <id> --query "75 inch QLED 4K TV"
    pdm run python run_pipeline.py stage2b <id> --match-all
    pdm run python run_pipeline.py stage3
    pdm run python run_pipeline.py stage4
    pdm run python run_pipeline.py stage4 --thread <gmail_thread_id>
    pdm run python run_pipeline.py stage4 --dry-run
    pdm run python run_pipeline.py stage5
    pdm run python run_pipeline.py stage5 --thread <supplier_thread_id>
    pdm run python run_pipeline.py stage5 --dry-run
    pdm run python run_pipeline.py stage5 --thread <id> --dry-run
    pdm run python run_pipeline.py stage6
    pdm run python run_pipeline.py test-outreach <alibaba_url>
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


def _triage_single_thread(gmail_thread_id: str):
    """Read-only triage of a single Gmail thread for diagnostics."""
    from app.pipeline.stages.s4_inbox_triage import (
        _extract_sender,
        _extract_subject,
        _extract_body,
        _get_ignore_emails,
        _is_no_reply_sender,
        _is_platform_notification,
        _triage_with_llm,
        _get_known_gmail_threads,
    )
    from app.services.gmail import GmailService

    gmail = GmailService()
    thread_data = gmail.get_thread(gmail_thread_id)
    messages = thread_data.get("messages", [])
    if not messages:
        log.warning("No messages in Gmail thread %s", gmail_thread_id)
        return

    latest = messages[-1]
    sender_name, sender_email = _extract_sender(latest)
    subject = _extract_subject(latest)
    body = _extract_body(latest)

    log.info("Gmail thread: %s", gmail_thread_id)
    log.info("  From: %s <%s>", sender_name, sender_email)
    log.info("  Subject: %s", subject)
    log.info("  Body preview: %s", body[:200])

    # Check tier 0: known supplier thread
    known_threads = _get_known_gmail_threads()
    if gmail_thread_id in known_threads:
        log.info(
            "  RESULT: Known supplier thread %d — would record reply directly",
            known_threads[gmail_thread_id],
        )
        return

    # Check tier 1: ignore list
    ignore_emails = _get_ignore_emails()
    if sender_email in ignore_emails:
        log.info("  RESULT: Sender is on ignore list — would auto-archive")
        return

    # Check tier 2: no-reply + platform notification
    if _is_no_reply_sender(sender_name, sender_email):
        if _is_platform_notification(subject, body):
            log.info(
                "  RESULT: Platform notification from no-reply sender — would archive"
            )
            return
        log.info(
            "  No-reply sender but not a recognized notification — falling through to LLM"
        )

    # Tier 3: LLM triage
    result = _triage_with_llm(sender_name, sender_email, subject, body)
    log.info("  RESULT (LLM triage):")
    log.info("    Action: %s", result.action)
    log.info("    Thread ID match: %s", result.thread_id)
    log.info("    Summary: %s", result.summary)
    log.info("    Reason: %s", result.reason)


def _triage_dry_run():
    """Poll full inbox and classify every thread, but don't archive or record anything."""
    from app.pipeline.stages.s4_inbox_triage import (
        _extract_sender,
        _extract_subject,
        _extract_body,
        _get_ignore_emails,
        _is_no_reply_sender,
        _is_platform_notification,
        _triage_with_llm,
        _get_known_gmail_threads,
    )
    from app.services.gmail import GmailService

    gmail = GmailService()
    ignore_emails = _get_ignore_emails()
    known_threads = _get_known_gmail_threads()
    unread = gmail.list_unread_threads()

    if not unread:
        log.info("DRY RUN — Inbox empty, nothing to triage")
        return

    log.info("DRY RUN — %d unread threads", len(unread))

    for thread_stub in unread:
        gmail_thread_id = thread_stub["id"]
        try:
            thread_data = gmail.get_thread(gmail_thread_id)
            messages = thread_data.get("messages", [])
            if not messages:
                continue

            latest = messages[-1]
            sender_name, sender_email = _extract_sender(latest)
            subject = _extract_subject(latest)
            body = _extract_body(latest)

            log.info(
                "[%s] From: %s <%s>  Subject: %s",
                gmail_thread_id,
                sender_name,
                sender_email,
                subject,
            )

            if gmail_thread_id in known_threads:
                log.info(
                    "  → Known supplier thread %d — would record reply",
                    known_threads[gmail_thread_id],
                )
                continue

            if sender_email in ignore_emails:
                log.info("  → Ignore list — would auto-archive")
                continue

            if _is_no_reply_sender(sender_name, sender_email):
                if _is_platform_notification(subject, body):
                    log.info("  → Platform notification — would archive")
                    continue
                log.info(
                    "  No-reply sender, not a known notification — falling through to LLM"
                )

            result = _triage_with_llm(sender_name, sender_email, subject, body)
            log.info("  → LLM: action=%s thread_id=%s", result.action, result.thread_id)
            log.info("    Summary: %s", result.summary)
            log.info("    Reason: %s", result.reason)

        except Exception:
            log.exception("Error triaging gmail thread %s", gmail_thread_id)


def cmd_stage4(args):
    if args.thread:
        _triage_single_thread(args.thread)
        return

    if args.dry_run:
        _triage_dry_run()
        return

    from app.pipeline.stages.s4_inbox_triage import triage_inbox

    counts = triage_inbox()
    log.info("Stage 4 complete. %s", counts)


def _negotiate_single_thread(thread_id: int, dry_run: bool = False):
    """Process a single supplier thread, optionally in dry-run mode."""
    from app.pipeline.stages.s5_negotiation import _process_thread
    from app.pipeline.agents.negotiation_agent import negotiate, build_message_history
    from app.pipeline.agents.match_agent import compare_products
    from app.db.database import SessionLocal
    from app.db.models.message import Message
    from app.db.models.supplier_thread import SupplierThread
    from app.services.gmail import GmailService

    if not dry_run:
        gmail = GmailService()
        status = _process_thread(thread_id, gmail)
        log.info("Thread %d processed: %s", thread_id, status)
        return

    # Dry-run mode: run agents but don't send Gmail or update DB
    with SessionLocal() as session:
        thread = session.get(SupplierThread, thread_id)
        if not thread:
            log.error("Thread %d not found", thread_id)
            return

        state = thread.state
        negotiation_rounds = thread.negotiation_rounds
        product_title = thread.source_product.title
        source_title = thread.source_product.title
        source_specs = thread.source_product.specs or {}
        supplier_title = thread.supplier_product.title
        supplier_specs = thread.supplier_product.specs or {}

        messages = (
            session.query(Message)
            .filter_by(thread_id=thread_id)
            .order_by(Message.sent_at)
            .all()
        )
        for m in messages:
            session.expunge(m)

    log.info(
        "DRY RUN — Thread %d (state=%s, rounds=%d)",
        thread_id,
        state,
        negotiation_rounds,
    )

    # Spec check on first reply (round 0)
    if state == "AWAITING_REPLY" and negotiation_rounds == 0:
        log.info("  Running spec check (dry-run)...")
        result = compare_products(
            reference_title=source_title,
            reference_specs=source_specs,
            candidate_title=supplier_title,
            candidate_details=supplier_specs,
        )
        log.info(
            "  Spec check result: match=%s confidence=%.2f",
            result.is_match,
            result.confidence,
        )
        log.info("  Reasoning: %s", result.reasoning)
        if result.key_differences:
            log.info("  Key differences: %s", result.key_differences)
        if not result.is_match:
            log.info("  Would close thread — spec check failed")
            return

    # Build message history and run negotiation
    inbound_messages = [m for m in messages if m.direction == "inbound"]
    if not inbound_messages:
        log.warning("  No inbound messages found for thread %d", thread_id)
        return

    latest_inbound = inbound_messages[-1]
    message_history = build_message_history(messages[:-1]) if len(messages) > 1 else []

    result = negotiate(
        message_history=message_history,
        latest_supplier_message=latest_inbound.body,
        negotiation_rounds=negotiation_rounds,
        product_title=product_title,
    )

    log.info("  Negotiation result:")
    log.info("    Action: %s", result.action)
    log.info(
        "    Reply text: %s", result.reply_text[:500] if result.reply_text else "(none)"
    )
    log.info("    Reasoning: %s", result.reasoning)
    eq = result.extracted_quote
    log.info(
        "    Extracted quote: price_usd=%s moq=%s lead_time=%s",
        eq.price_usd,
        eq.moq,
        eq.lead_time,
    )
    if eq.currency_note:
        log.info("    Currency note: %s", eq.currency_note)


def cmd_stage5(args):
    if args.thread:
        _negotiate_single_thread(args.thread, dry_run=args.dry_run)
        return

    if args.dry_run:
        # Dry-run sweep: iterate all ready threads in dry-run mode
        from app.pipeline.stages.s5_negotiation import _get_ready_threads

        thread_ids = _get_ready_threads()
        if not thread_ids:
            log.info("No threads ready for negotiation")
            return
        log.info("DRY RUN — Processing %d ready threads", len(thread_ids))
        for tid in thread_ids:
            try:
                _negotiate_single_thread(tid, dry_run=True)
            except Exception:
                log.exception("Error dry-running thread %d", tid)
        return

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


def cmd_test_outreach(args):
    from app.pipeline.agents.inquiry_agent import send_inquiry_via_agent
    from app.pipeline.stages.s3_outreach import (
        _authenticate_platform,
        _create_agent_session,
        _detach_browser,
        _release_session,
    )
    from app.services.browser import BrowserSession
    from app.services.platforms.alibaba import Platform

    platform = Platform()
    message = (
        "Hi, we are a leading Australian distributor interested in this product. "
        "Could you please provide your best FOB pricing, MOQ, and lead time? "
        "For further correspondence please contact us at "
        f"{args.email or 'sourcing.agent@example.com'}. Thanks, the agent."
    )

    log.info("Authenticating on Alibaba...")
    context_id = _authenticate_platform(platform)
    log.info("Auth context saved.")

    if args.agent:
        from app.pipeline.agents.inquiry_agent import InquiryStatus

        session_id = _create_agent_session(context_id)
        log.info("Agent session: %s", session_id)
        log.info("Sending test inquiry via agent to %s", args.url)
        try:
            result = send_inquiry_via_agent(
                session_id,
                args.url,
                message,
                cleanup=False,
                platform_prompt=platform.inquiry_agent_prompt,
            )
            log.info("Result: status=%s reason=%s", result.status, result.reason)
            if result.status == InquiryStatus.LOGIN_REQUIRED:
                _release_session(session_id)
                log.info("Re-authenticating after login prompt...")
                context_id = _authenticate_platform(platform)
                session_id = _create_agent_session(context_id)
                result = send_inquiry_via_agent(
                    session_id,
                    args.url,
                    message,
                    cleanup=False,
                    platform_prompt=platform.inquiry_agent_prompt,
                )
                log.info(
                    "Retry result: status=%s reason=%s", result.status, result.reason
                )
        except Exception:
            log.exception("Agent failed")
        finally:
            _release_session(session_id)
    else:
        log.info("Sending test inquiry (deterministic) to %s", args.url)
        browser = BrowserSession(
            proxy_country="AU",
            context_id=context_id,
            keep_alive=True,
        )
        browser.__enter__()
        session_id = browser.session_id
        try:
            success = platform.send_inquiry(browser.page, args.url, message)
            if success:
                log.info("Inquiry sent successfully")
            else:
                log.warning("Inquiry not confirmed — retrying with agent")
                _detach_browser(browser)
                result = send_inquiry_via_agent(
                    session_id,
                    args.url,
                    message,
                    cleanup=False,
                    platform_prompt=platform.inquiry_agent_prompt,
                )
                log.info("Result: status=%s reason=%s", result.status, result.reason)
                _release_session(session_id)
                return
        except Exception:
            log.exception("Deterministic flow failed — retrying with agent")
            _detach_browser(browser)
            result = send_inquiry_via_agent(
                session_id,
                args.url,
                message,
                cleanup=False,
                platform_prompt=platform.inquiry_agent_prompt,
            )
            log.info("Result: status=%s reason=%s", result.status, result.reason)
            _release_session(session_id)
            return
        browser.__exit__(None, None, None)


def main():
    parser = argparse.ArgumentParser(description="Run pipeline stages incrementally")
    sub = parser.add_subparsers(dest="command", required=True)

    s1 = sub.add_parser("stage1", help="Extract specs from a Kogan URL")
    s1.add_argument("url", help="Kogan product URL")
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
    s4.add_argument(
        "--thread",
        help="Triage a single Gmail thread ID (read-only diagnostic)",
    )
    s4.add_argument(
        "--dry-run",
        action="store_true",
        help="Triage full inbox but don't archive or record anything",
    )
    s4.set_defaults(func=cmd_stage4)

    s5 = sub.add_parser("stage5", help="Process negotiations")
    s5.add_argument(
        "--thread",
        type=int,
        help="Process a single supplier thread ID",
    )
    s5.add_argument(
        "--dry-run",
        action="store_true",
        help="Run negotiation agents but don't send Gmail or update DB",
    )
    s5.set_defaults(func=cmd_stage5)

    s6 = sub.add_parser("stage6", help="Update output Google Sheet")
    s6.set_defaults(func=cmd_stage6)

    to = sub.add_parser(
        "test-outreach", help="Test inquiry against any Alibaba URL (no DB)"
    )
    to.add_argument("url", help="Alibaba product URL")
    to.add_argument("--email", help="Contact email to include in message")
    to.add_argument(
        "--agent",
        action="store_true",
        help="Skip deterministic flow, go direct to LLM agent",
    )
    to.set_defaults(func=cmd_test_outreach)

    st = sub.add_parser("status", help="Show pipeline status")
    st.add_argument("source_product_id", type=int, nargs="?", default=None)
    st.set_defaults(func=cmd_status)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
