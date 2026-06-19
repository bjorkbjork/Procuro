"""Kmart AU product page parsing. Extracts product title and structured specs
from the Kmart.com.au product page HTML."""

from bs4 import BeautifulSoup


def parse_title(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1", {"data-testid": "product-title"})
    if h1:
        return h1.get_text(strip=True)
    title_tag = soup.find("title")
    if not title_tag:
        return ""
    raw = title_tag.get_text(strip=True)
    return raw.removesuffix(" - Kmart").strip()


def parse_specs(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    for acc in soup.find_all(
        class_=lambda c: c and "MuiAccordion-root" in c
    ):
        summary = acc.find(
            class_=lambda c: c and "MuiAccordionSummary" in c
        )
        if not summary or "Description" not in summary.get_text():
            continue
        details = acc.find(
            class_=lambda c: c and "MuiAccordionDetails" in c
        )
        if not details:
            continue
        inner = details.find("div")
        if not inner:
            continue

        specs = {}
        current_group = "General"
        for child in inner.children:
            if not hasattr(child, "name") or not child.name:
                continue
            if child.name == "strong":
                text = child.get_text(strip=True)
                if text:
                    current_group = text
            elif child.name == "ul":
                group_specs = {}
                for li in child.find_all("li"):
                    text = li.get_text(strip=True)
                    if ":" in text:
                        key, _, val = text.partition(":")
                        key, val = key.strip(), val.strip()
                        if key and val:
                            group_specs[key] = val
                if group_specs:
                    if current_group in specs:
                        specs[current_group].update(group_specs)
                    else:
                        specs[current_group] = group_specs
        return specs
    return {}


def slug_from_url(url: str) -> str:
    return url.rstrip("/").split("/")[-1]
