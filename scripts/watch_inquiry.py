"""Step through the Alibaba inquiry flow to debug submit behavior.
Logs all network traffic and captures DOM state at each step."""

import json
import re
import time

from app.services.browser import BrowserSession
from app.services.platforms.alibaba.service import (
    INQUIRY_BUTTON,
    INQUIRY_SUBMIT,
    INQUIRY_SUCCESS,
    INQUIRY_TEXTAREA,
    _get_inquiry_frame,
    login_alibaba,
)

PRODUCT_URL = (
    "https://www.alibaba.com/product-detail/"
    "Convenient-Installation-Bed-Lift-Space-Saving_1601763389102.html"
)

TEST_MESSAGE = "Hi, this is a test inquiry. Please ignore."


def save_html(page, label):
    frame = page.frame(url=re.compile(r"message\.alibaba\.com"))
    if not frame:
        print(f"  [{label}] No inquiry frame found")
        return
    path = f"html_test_fixtures/inquiry_frame_{label}.html"
    with open(path, "w") as f:
        f.write(frame.content())
    print(f"  [{label}] Saved to {path}")


def step(name):
    print(f"\n{'─'*50}")
    print(f"STEP: {name}")
    print(f"{'─'*50}")
    input("Press Enter to continue...")


def main():
    with BrowserSession(keep_alive=True) as session:
        page = session.page
        print(f"\n{'='*60}")
        print(f"LIVE URL: {session.live_url}")
        print(f"{'='*60}")

        # Log ALL network after submit
        log_all = [False]

        def on_request(request):
            if log_all[0]:
                method = request.method
                url = request.url[:150]
                post = request.post_data[:300] if request.post_data else None
                print(f"  >> {method} {url}")
                if post:
                    print(f"     BODY: {post[:200]}")

        def on_response(response):
            if log_all[0]:
                print(f"  << {response.status} {response.url[:150]}")

        page.on("request", on_request)
        page.on("response", on_response)

        step("Login")
        login_alibaba(page)
        print("Logged in!")

        step("Navigate to product")
        page.goto(PRODUCT_URL, timeout=60_000)

        step("Click inquiry button")
        page.click(INQUIRY_BUTTON)
        frame = _get_inquiry_frame(page)
        print(f"Got frame: {frame.url[:80]}")
        save_html(page, "1_opened")

        step("Fill message")
        frame.wait_for_selector(INQUIRY_TEXTAREA, timeout=10_000)
        frame.fill(INQUIRY_TEXTAREA, TEST_MESSAGE)
        time.sleep(1)

        # Check submit state
        btn_disabled = frame.evaluate(
            f"sel => document.querySelector(sel)?.disabled", INQUIRY_SUBMIT
        )
        btn_classes = frame.evaluate(
            f"sel => document.querySelector(sel)?.className", INQUIRY_SUBMIT
        )
        print(f"  Submit disabled: {btn_disabled}")
        print(f"  Submit classes: {btn_classes}")
        save_html(page, "2_filled")

        step("Click submit via JS (logging ALL network)")
        log_all[0] = True
        frame.evaluate("sel => document.querySelector(sel).click()", INQUIRY_SUBMIT)
        print("  JS click dispatched, waiting 5s...")
        time.sleep(5)
        log_all[0] = False

        # Check for success
        success_el = frame.locator(INQUIRY_SUCCESS)
        print(f"\n  Success element count: {success_el.count()}")
        if success_el.count() > 0:
            print(f"  Success visible: {success_el.is_visible()}")
            print(f"  Success text: {success_el.text_content()[:200]}")
        save_html(page, "3_after_submit")

        # Also try Playwright click for comparison
        step("Try Playwright click for comparison")
        log_all[0] = True
        frame.locator(INQUIRY_SUBMIT).click()
        print("  Playwright click dispatched, waiting 5s...")
        time.sleep(5)
        log_all[0] = False
        save_html(page, "4_after_pw_click")

        print(f"\nSession: {session.live_url}")
        input("\nPress Enter to exit...")


if __name__ == "__main__":
    main()
