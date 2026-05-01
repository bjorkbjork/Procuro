"""Live test: Alibaba supplier search API."""

from app.services.alibaba import search_suppliers


def test_search():
    results = search_suppliers(
        query="75 inch QLED 4K television",
        core_product="QLED television",
        attributes="75 inch,4K",
        page_size=5,
    )

    assert len(results) > 0, "Expected at least one result"

    for r in results:
        print(f"{r['company_name']}")
        print(f"  Product: {r['title'][:80]}")
        print(f"  Price: {r['price']}  MOQ: {r['moq']}")
        print(f"  Profile: {r['profile_url']}")
        print(f"  {r['country']} | {r['years_on_platform']} | Rating: {r['review_score']} ({r['review_count']} reviews)")
        print(f"  Certs: {', '.join(r['certifications']) or 'none'}")
        print()

    print(f"Total: {len(results)} verified suppliers")


if __name__ == "__main__":
    test_search()
