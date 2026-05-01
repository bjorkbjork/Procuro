"""Stage 3: Send initial outreach to suppliers via platform inquiry forms.

For each SupplierThread in state NEW, builds a message from the outreach
template and submits it through the platform's inquiry form. Updates
thread state to OUTREACH_SENT and logs the message."""

import logging

from app.base.config import settings
from app.db.database import SessionLocal
from app.db.models.message import Message
from app.db.models.source_product import SourceProduct
from app.db.models.supplier_product import SupplierProduct
from app.db.models.supplier_thread import SupplierThread
from app.services.browser import BrowserSession
from app.services.platforms import get_platforms
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


def send_outreach() -> int:
    """Send outreach for all NEW supplier threads. Returns count of inquiries sent."""
    platforms = {p.platform.value: p for p in get_platforms()}
    grouped = _get_threads_by_platform()
    sent_count = 0

    for platform_name, thread_infos in grouped.items():
        platform = platforms.get(platform_name)
        if not platform:
            log.warning("No platform registered for '%s' — skipping", platform_name)
            continue

        log.info(
            "Sending %d inquiries on %s", len(thread_infos), platform_name,
        )

        with BrowserSession() as browser:
            platform.login(browser.page)

            for info in thread_infos:
                thread_id = info["thread_id"]
                product_url = info["product_url"]
                source_product = info["source_product"]
                message = _build_message(source_product)

                with SessionLocal() as session:
                    try:
                        success = platform.send_inquiry(
                            browser.page, product_url, message,
                        )
                    except Exception as exc:
                        session.rollback()
                        log.exception(
                            "Failed to send inquiry for thread %d (%s)",
                            thread_id, product_url,
                        )
                        if "Target" in str(exc) and "closed" in str(exc):
                            log.error("Browser session dead — aborting remaining inquiries")
                            break
                        continue

                    if not success:
                        session.rollback()
                        log.warning(
                            "Inquiry not confirmed for thread %d (%s)",
                            thread_id, product_url,
                        )
                        continue

                    thread = session.get(SupplierThread, thread_id)
                    thread.state = "OUTREACH_SENT"
                    session.add(Message(
                        thread_id=thread_id,
                        direction="outbound",
                        subject="Initial outreach",
                        body=message,
                    ))
                    session.commit()

                sent_count += 1
                log.info("Outreach sent for thread %d (%s)", thread_id, product_url)

    log.info("Stage 3 complete: %d inquiries sent", sent_count)
    return sent_count
