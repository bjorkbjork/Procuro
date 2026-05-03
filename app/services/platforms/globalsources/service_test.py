"""Tests for GlobalSources service — unit tests for response parsing, integration
test for live search via Browserbase."""

import pytest

from app.services.platforms.globalsources.service import (
    _strip_html,
    parse_search_response,
    search_suppliers,
)

GS_API_RESPONSE = {
    "code": "200",
    "msg": "success",
    "data": {
        "pageNum": 1,
        "pageSize": 80,
        "total": 4425,
        "totalPage": 56,
        "list": [
            {
                "id": 1212888159,
                "orgId": 2008857041415,
                "productId": 1212888159,
                "productName": (
                    "Manufacturer <strong>55 inches</strong> oled smart "
                    "<strong>4k tv</strong> transparent oled <strong>tv</strong>"
                ),
                "price": "160.00",
                "priceUnit": "US $",
                "minOrder": 300,
                "minOrderQuantity": 300,
                "minOrderUnit": "Pieces",
                "minOrderSingleUnit": "Piece",
                "companyName": "Guangzhou Soho Industry Co., Limited",
                "desktopProductDetailUrl": "/OLED-TV/55-inch-smart-tv-1212888159p.htm",
                "modelNumber": "AL1001-6753-#0064",
                "vmFlag": True,
                "vsFlag": True,
                "supplier": {
                    "id": 2008857041415,
                    "supplierName": "Guangzhou Soho Industry Co., Limited",
                    "level": 5,
                },
            },
            {
                "id": 9999999,
                "orgId": 1234567890,
                "productId": 9999999,
                "productName": "No MOQ product",
                "price": "",
                "priceUnit": "US $",
                "minOrder": 0,
                "minOrderQuantity": 0,
                "minOrderUnit": "",
                "minOrderSingleUnit": "",
                "companyName": "Some Other Co.",
                "desktopProductDetailUrl": "/TV/other-9999999p.htm",
                "vmFlag": False,
                "vsFlag": False,
                "supplier": {"id": 1234567890, "supplierName": "Some Other Co."},
            },
        ],
    },
}


class TestStripHtml:
    def test_removes_strong_tags(self):
        assert _strip_html("<strong>bold</strong>") == "bold"

    def test_removes_nested_tags(self):
        assert _strip_html("a <b>b <i>c</i></b> d") == "a b c d"

    def test_plain_text_unchanged(self):
        assert _strip_html("no tags here") == "no tags here"

    def test_empty_string(self):
        assert _strip_html("") == ""


class TestParseSearchResponse:
    def test_parses_valid_response(self):
        results = parse_search_response(GS_API_RESPONSE)
        assert len(results) == 2

    def test_first_result_fields(self):
        results = parse_search_response(GS_API_RESPONSE)
        r = results[0]
        assert r["product_id"] == "1212888159"
        assert r["product_url"] == (
            "https://www.globalsources.com/OLED-TV/55-inch-smart-tv-1212888159p.htm"
        )
        assert (
            r["title"] == "Manufacturer 55 inches oled smart 4k tv transparent oled tv"
        )
        assert r["price"] == "160.00"
        assert r["moq"] == "300 Piece"
        assert r["company_name"] == "Guangzhou Soho Industry Co., Limited"
        assert r["company_id"] == "2008857041415"
        assert r["profile_url"] == (
            "https://www.globalsources.com/suppliers/2008857041415"
        )

    def test_zero_moq_omitted(self):
        results = parse_search_response(GS_API_RESPONSE)
        assert results[1]["moq"] == ""

    def test_html_stripped_from_title(self):
        results = parse_search_response(GS_API_RESPONSE)
        assert "<strong>" not in results[0]["title"]

    def test_error_response_returns_empty(self):
        assert parse_search_response({"code": "500", "msg": "error"}) == []

    def test_missing_data_returns_empty(self):
        assert parse_search_response({"code": "200", "data": {}}) == []

    def test_empty_list_returns_empty(self):
        assert parse_search_response({"code": "200", "data": {"list": []}}) == []

    def test_missing_fields_dont_crash(self):
        data = {"code": "200", "data": {"list": [{}]}}
        results = parse_search_response(data)
        assert len(results) == 1
        assert results[0]["product_id"] == ""
        assert results[0]["product_url"] == ""
        assert results[0]["title"] == ""


@pytest.mark.integration
class TestSearchSuppliers:
    """Live test — hits the real GS API via Browserbase."""

    def test_search(self):
        results = search_suppliers(
            query="55 inch QLED 4K television",
            page_size=5,
        )

        assert len(results) > 0
        for r in results:
            assert r["product_id"]
            assert r["product_url"]
            assert r["title"]
            assert r["company_name"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
