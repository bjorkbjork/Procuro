"""Stage 3: Send initial outreach to suppliers via platform inquiry forms.

For each SupplierThread in state NEW, builds a message from the outreach
template and submits it through the platform's inquiry form. Runs the full
deterministic Playwright flow first (threaded, with auth context). Any
threads that fail are retried via the LLM inquiry agent with the same
auth context."""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.agent.inquiry_agent import InquiryStatus, send_inquiry_via_agent
from app.base.config import settings
from app.db.database import SessionLocal
from app.db.models.message import Message
from app.db.models.source_product import SourceProduct
from app.db.models.supplier_product import SupplierProduct
from app.db.models.supplier_thread import SupplierThread
from app.services.browser import BrowserSession, create_context
from app.services.platforms import get_platforms
from app.services.platforms.alibaba.service import WholesaleProductError
from app.services.platforms.platform import SupplierPlatform

log = logging.getLogger(__name__)

OUTREACH_TEMPLATE = """\
Hi,

We are looking to source the following product:

{spec_block}

\
We are looking to form long term relationships for consistent orders. Please provide \
your best pricing, lead time and MOQ.

The product we are looking for is to have the specification above and essentially be \
the same as this product: {source_url}

Please only respond if you are able to meet these requirements. Our order quantities \
are typically very large and frequent throughout the year.

For further correspondence, please contact us directly via email at {email}.

Many Thanks, the agent."""


def _format_spec_block(specs: dict) -> str:
    lines = []
    for group_name, group_specs in specs.items():
        lines.append(f"{group_name}:")
        for key, val in group_specs.items():
            lines.append(f"  {key}: {val}")
    return "\n".join(lines)


def _build_message(source_product: SourceProduct) -> str:
    return OUTREACH_TEMPLATE.format(
        spec_block=_format_spec_block(source_product.specs),
        source_url=source_product.url,
        email=settings.GMAIL_ACCOUNT,
    )


def _get_threads_by_platform() -> dict[str, list[dict]]:
    """Load NEW threads grouped by platform, with related objects."""
    with SessionLocal() as session:
        threads = (
            session.query(SupplierThread)
            .filter_by(state="NEW")
            .all()
        )
        grouped: dict[str, list[dict]] = {}
        for thread in threads:
            sp = session.get(SupplierProduct, thread.supplier_product_id)
            source = session.get(SourceProduct, thread.source_product_id)
            platform_name = sp.platform
            grouped.setdefault(platform_name, []).append({
                "thread_id": thread.id,
                "product_url": sp.product_url,
                "source_product": source,
            })
    return grouped


def _record_success(thread_id: int, message: str) -> None:
    with SessionLocal() as session:
        thread = session.get(SupplierThread, thread_id)
        thread.state = "OUTREACH_SENT"
        session.add(Message(
            thread_id=thread_id,
            direction="outbound",
            subject="Initial outreach",
            body=message,
        ))
        session.commit()


def _send_single_inquiry(
    platform: SupplierPlatform, thread_id: int, product_url: str, message: str,
    context_id: str, thread_name: str = "",
) -> bool:
    """Send one inquiry in its own browser session. Returns True on success."""
    if thread_name:
        threading.current_thread().name = thread_name
    with SessionLocal() as session:
        try:
            with BrowserSession(proxy_country="AU", context_id=context_id) as browser:
                success = platform.send_inquiry(
                    browser.page, product_url, message,
                )
        except WholesaleProductError:
            thread = session.get(SupplierThread, thread_id)
            thread.state = "UNPROCESSABLE"
            session.commit()
            log.info(
                "Thread %d skipped — wholesale-only product (%s)",
                thread_id, product_url,
            )
            return False
        except Exception:
            session.rollback()
            log.exception(
                "Failed to send inquiry for thread %d (%s)",
                thread_id, product_url,
            )
            return False

        if not success:
            log.warning(
                "Inquiry not confirmed for thread %d (%s)",
                thread_id, product_url,
            )
            return False

    _record_success(thread_id, message)
    log.info("Outreach sent for thread %d (%s)", thread_id, product_url)
    return True


def _retry_with_agent(
    thread_id: int, product_url: str, message: str,
    context_id: str,
) -> bool:
    """Retry a failed inquiry using the LLM agent with Browserbase MCP."""
    log.info("Retrying thread %d via LLM agent (%s)", thread_id, product_url)
    try:
        result = send_inquiry_via_agent(product_url, message, context_id)
    except Exception:
        log.exception("Agent retry error for thread %d (%s)", thread_id, product_url)
        return False

    if result.status == InquiryStatus.WHOLESALE:
        with SessionLocal() as session:
            thread = session.get(SupplierThread, thread_id)
            thread.state = "UNPROCESSABLE"
            session.commit()
        log.info("Agent: thread %d is wholesale-only (%s)", thread_id, product_url)
        return False

    if result.status == InquiryStatus.FAILED:
        log.warning(
            "Agent retry failed for thread %d (%s): %s",
            thread_id, product_url, result.reason,
        )
        return False

    _record_success(thread_id, message)
    log.info("Agent retry succeeded for thread %d (%s)", thread_id, product_url)
    return True


def send_outreach(agent_only: bool = False) -> int:
    """Send outreach for all NEW supplier threads. Returns count of inquiries sent.

    When agent_only=True, skips the deterministic Playwright flow and sends
    all inquiries via the LLM agent directly.
    """
    platforms = {p.platform.value: p for p in get_platforms()}
    grouped = _get_threads_by_platform()
    sent_count = 0

    for platform_name, thread_infos in grouped.items():
        platform = platforms.get(platform_name)
        if not platform:
            log.warning("No platform registered for '%s' — skipping", platform_name)
            continue

        log.info(
            "Sending %d inquiries on %s%s",
            len(thread_infos), platform_name,
            " (agent only)" if agent_only else "",
        )

        context_id = create_context()
        with BrowserSession(
            proxy_country="AU", context_id=context_id, persist_context=True,
        ) as browser:
            platform.login(browser.page, session_url=browser.live_url or "")
        log.info("Auth context saved")

        infos_with_messages = []
        for info in thread_infos:
            info["message"] = _build_message(info["source_product"])
            infos_with_messages.append(info)

        if agent_only:
            # Send all via LLM agent directly
            for info in infos_with_messages:
                if _retry_with_agent(
                    info["thread_id"], info["product_url"], info["message"],
                    context_id=context_id,
                ):
                    sent_count += 1
            continue

        # Pass 1: deterministic Playwright flow (threaded)
        failed: list[dict] = []
        futures = {}
        with ThreadPoolExecutor(max_workers=settings.MAX_WORKERS) as pool:
            for info in infos_with_messages:
                slug = platform.url_slug(info["product_url"])
                future = pool.submit(
                    _send_single_inquiry,
                    platform, info["thread_id"], info["product_url"], info["message"],
                    context_id=context_id, thread_name=slug,
                )
                futures[future] = info

            for future in as_completed(futures):
                info = futures[future]
                if future.result():
                    sent_count += 1
                else:
                    with SessionLocal() as session:
                        thread = session.get(SupplierThread, info["thread_id"])
                        if thread.state == "NEW":
                            failed.append(info)

        # Pass 2: retry failures with LLM agent (sequential)
        if failed:
            log.info(
                "Retrying %d failed inquiries with LLM agent", len(failed),
            )
            for info in failed:
                if _retry_with_agent(
                    info["thread_id"], info["product_url"], info["message"],
                    context_id=context_id,
                ):
                    sent_count += 1

    log.info("Stage 3 complete: %d inquiries sent", sent_count)
    return sent_count
