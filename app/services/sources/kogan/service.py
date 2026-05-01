"""Kogan product page parsing. Extracts product title and structured specs
from the Kogan.com product page HTML."""

from bs4 import BeautifulSoup


def parse_title(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find("title")
    if not title_tag:
        return ""
    raw = title_tag.get_text(strip=True)
    raw = raw.removeprefix("Buy ").removesuffix(" | Kogan.com")
    return raw.split(" Online")[0].strip()


def parse_specs(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    for details in soup.find_all("details"):
        summary = details.find("summary")
        if not summary or summary.get_text(strip=True) != "Specifications":
            continue
        specs = {}
        for group in details.find_all("div", class_="mb-sm"):
            heading = group.find("h5")
            group_name = heading.get_text(strip=True) if heading else "General"
            group_specs = {}
            for row in group.find_all("div", class_="flex"):
                spans = row.find_all("span")
                if len(spans) == 2:
                    key = spans[0].get_text(strip=True)
                    val = spans[1].get_text(strip=True)
                    group_specs[key] = val
            if group_specs:
                specs[group_name] = group_specs
        return specs
    return {}


def slug_from_url(url: str) -> str:
    return url.rstrip("/").split("/")[-1]
