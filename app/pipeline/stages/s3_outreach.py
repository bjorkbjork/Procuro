"""Stage 3: Send initial outreach to suppliers via platform inquiry forms.

For each SupplierThread in state NEW, builds a message from the outreach
template and submits it through the platform's inquiry form. Each thread
runs the deterministic Playwright flow first; on failure the LLM inquiry
agent takes over immediately in the same worker thread while the browser
session is still live."""

import logging

from app.pipeline.agents.inquiry_agent import (
    InquiryResult,
    InquiryStatus,
    send_inquiry_via_agent,
)
from app.pipeline.browser_executor import BrowserFallbackExecutor, FallbackResult
from app.base.config import settings, browserbase_settings
from app.db.database import SessionLocal
from app.db.models.message import Message
from app.db.models.source_product import SourceProduct
from app.db.models.supplier_product import SupplierProduct
from app.db.models.supplier_thread import SupplierThread
from app.services.browser import authenticate_platform, bb
from app.services.platforms import get_platforms
from app.services.platforms.alibaba.service import WholesaleProductError

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
        threads = session.query(SupplierThread).filter_by(state="NEW").all()
        grouped: dict[str, list[dict]] = {}
        for thread in threads:
            sp = session.get(SupplierProduct, thread.supplier_product_id)
            source = session.get(SourceProduct, thread.source_product_id)
            platform_name = sp.platform
            grouped.setdefault(platform_name, []).append(
                {
                    "thread_id": thread.id,
                    "product_url": sp.product_url,
                    "source_product": source,
                }
            )
    return grouped


def _record_success(thread_id: int, message: str) -> None:
    with SessionLocal() as session:
        thread = session.get(SupplierThread, thread_id)
        thread.state = "OUTREACH_SENT"
        session.add(
            Message(
                thread_id=thread_id,
                direction="outbound",
                subject="Initial outreach",
                body=message,
            )
        )
        session.commit()


def _retry_with_agent(
    thread_id: int,
    product_url: str,
    message: str,
    session_id: str,
    *,
    cleanup: bool = True,
    platform_prompt: str = "",
) -> InquiryResult:
    """Retry a failed inquiry by dropping the LLM agent into the live session."""
    log.info("Retrying thread %d via LLM agent (%s)", thread_id, product_url)
    try:
        result = send_inquiry_via_agent(
            session_id,
            product_url,
            message,
            cleanup=cleanup,
            platform_prompt=platform_prompt,
        )
    except Exception:
        log.exception("Agent retry error for thread %d (%s)", thread_id, product_url)
        return InquiryResult(status=InquiryStatus.FAILED, reason="Agent exception")

    bb.sessions.update(session_id, status="REQUEST_RELEASE")

    if result.status == InquiryStatus.WHOLESALE:
        with SessionLocal() as session:
            thread = session.get(SupplierThread, thread_id)
            thread.state = "UNPROCESSABLE"
            session.commit()
        log.info("Agent: thread %d is wholesale-only (%s)", thread_id, product_url)

    elif result.status == InquiryStatus.FAILED:
        log.warning(
            "Agent retry failed for thread %d (%s): %s",
            thread_id,
            product_url,
            result.reason,
        )

    elif result.status == InquiryStatus.LOGIN_REQUIRED:
        log.warning("Agent hit login page for thread %d (%s)", thread_id, product_url)

    elif result.status == InquiryStatus.SENT:
        log.info("Agent retry succeeded for thread %d (%s)", thread_id, product_url)

    return result


def _create_agent_session(context_id: str) -> str:
    """Create a Browserbase session with AU proxy for agent-only mode."""
    session = bb.sessions.create(
        project_id=browserbase_settings.BROWSERBASE_PROJECT_ID,
        keep_alive=True,
        region="ap-southeast-1",
        proxies=[
            {"type": "browserbase", "geolocation": {"country": "AU", "city": "SYDNEY"}}
        ],
        browser_settings={
            "context": {"id": context_id, "persist": False},
        },
    )
    return session.id


class OutreachExecutor(BrowserFallbackExecutor):

    @property
    def stage(self) -> str:
        return "s3_outreach"

    @property
    def action(self) -> str:
        return "send_inquiry"

    def get_work_items(self) -> dict[str, list[dict]]:
        grouped = _get_threads_by_platform()
        for _platform_name, infos in grouped.items():
            for info in infos:
                info["message"] = _build_message(info["source_product"])
        return grouped

    def deterministic_action(self, item: dict, page, platform, context_id: str):
        try:
            success = platform.send_inquiry(
                page,
                item["product_url"],
                item["message"],
            )
        except WholesaleProductError:
            with SessionLocal() as session:
                thread = session.get(SupplierThread, item["thread_id"])
                thread.state = "UNPROCESSABLE"
                session.commit()
            log.info(
                "Thread %d skipped — wholesale-only product (%s)",
                item["thread_id"],
                item["product_url"],
            )
            return None

        if not success:
            raise RuntimeError("Inquiry not confirmed")
        return True

    def agent_fallback(self, item: dict, session_id: str, platform) -> FallbackResult:
        result = _retry_with_agent(
            item["thread_id"],
            item["product_url"],
            item["message"],
            session_id=session_id,
            platform_prompt=platform.inquiry_agent_prompt,
        )
        return FallbackResult(
            success=(result.status == InquiryStatus.SENT),
            login_required=(result.status == InquiryStatus.LOGIN_REQUIRED),
            result=True,
        )

    def on_success(self, item: dict, result) -> None:
        _record_success(item["thread_id"], item["message"])

    def thread_label(self, item: dict) -> str:
        return item.get("product_url", "").rsplit("/", 1)[-1][:40] or str(
            item["thread_id"]
        )

    def get_thread_id(self, item: dict) -> int | None:
        return item.get("thread_id")


def send_outreach(agent_only: bool = False) -> int:
    """Send outreach for all NEW supplier threads. Returns count of inquiries sent.

    When agent_only=True, skips the deterministic Playwright flow and sends
    all inquiries via the LLM agent directly.
    """
    if agent_only:
        return _send_agent_only()

    results = OutreachExecutor().execute()
    sent_count = sum(1 for _, r in results if r is not None)
    log.info("Stage 3 complete: %d inquiries sent", sent_count)
    return sent_count


def _send_agent_only() -> int:
    """Send all inquiries via LLM agent — no deterministic path."""
    platforms = {p.platform.value: p for p in get_platforms()}
    grouped = _get_threads_by_platform()
    sent_count = 0

    for platform_name, thread_infos in grouped.items():
        platform = platforms.get(platform_name)
        if not platform:
            log.warning("No platform registered for '%s' — skipping", platform_name)
            continue

        log.info(
            "Sending %d inquiries on %s (agent only)",
            len(thread_infos),
            platform_name,
        )

        context_id = authenticate_platform(platform)

        for info in thread_infos:
            info["message"] = _build_message(info["source_product"])
            session_id = _create_agent_session(context_id)
            result = _retry_with_agent(
                info["thread_id"],
                info["product_url"],
                info["message"],
                session_id=session_id,
                cleanup=False,
                platform_prompt=platform.inquiry_agent_prompt,
            )
            if result.status == InquiryStatus.SENT:
                _record_success(info["thread_id"], info["message"])
                sent_count += 1
            elif result.status == InquiryStatus.LOGIN_REQUIRED:
                log.info("Re-authenticating on %s after login prompt", platform_name)
                context_id = authenticate_platform(platform)
                session_id = _create_agent_session(context_id)
                result = _retry_with_agent(
                    info["thread_id"],
                    info["product_url"],
                    info["message"],
                    session_id=session_id,
                    cleanup=False,
                    platform_prompt=platform.inquiry_agent_prompt,
                )
                if result.status == InquiryStatus.SENT:
                    _record_success(info["thread_id"], info["message"])
                    sent_count += 1

    log.info("Stage 3 complete: %d inquiries sent (agent only)", sent_count)
    return sent_count
