"""Tests for GlobalSources service — unit tests for response parsing, protocol
compliance, and integration test for live search via Browserbase."""

import pytest

from app.db.models.enums import Platform as PlatformEnum
from app.services.platforms.globalsources import Platform
from app.services.platforms.globalsources.service import (
    _strip_html,
    parse_product_specs,
    parse_product_title,
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


class TestPlatformProtocol:
    """Verify the Platform class satisfies the SupplierPlatform protocol."""

    def test_platform_enum(self):
        p = Platform()
        assert p.platform == PlatformEnum.GLOBALSOURCES

    def test_has_spec_selector(self):
        p = Platform()
        assert isinstance(p.spec_selector, str)
        assert len(p.spec_selector) > 0

    def test_has_inquiry_agent_prompt(self):
        p = Platform()
        assert isinstance(p.inquiry_agent_prompt, str)
        assert "GlobalSources" in p.inquiry_agent_prompt

    def test_has_messaging_agent_prompt(self):
        p = Platform()
        assert isinstance(p.messaging_agent_prompt, str)
        assert "message" in p.messaging_agent_prompt.lower()

    def test_search_callable(self):
        assert callable(Platform().search)

    def test_parse_specs_callable(self):
        assert callable(Platform().parse_specs)

    def test_parse_title_callable(self):
        assert callable(Platform().parse_title)

    def test_login_callable(self):
        assert callable(Platform().login)

    def test_send_inquiry_callable(self):
        assert callable(Platform().send_inquiry)

    def test_read_platform_messages_raises_not_implemented(self):
        p = Platform()
        with pytest.raises(NotImplementedError):
            p.read_platform_messages(None)

    def test_send_platform_reply_raises_not_implemented(self):
        p = Platform()
        with pytest.raises(NotImplementedError):
            p.send_platform_reply(None, "http://example.com", "test")

    def test_url_slug(self):
        p = Platform()
        url = "https://www.globalsources.com/OLED-TV/55-inch-smart-tv-1212888159p.htm"
        assert p.url_slug(url) == "55-inch-smart-tv-1212888159p"


class TestParseProductTitle:
    def test_strips_globalsources_suffix(self):
        html = "<html><head><title>55 inch TV | GlobalSources</title></head></html>"
        assert parse_product_title(html) == "55 inch TV"

    def test_strips_dash_suffix(self):
        html = "<html><head><title>55 inch TV - GlobalSources</title></head></html>"
        assert parse_product_title(html) == "55 inch TV"

    def test_no_suffix(self):
        html = "<html><head><title>55 inch TV</title></head></html>"
        assert parse_product_title(html) == "55 inch TV"

    def test_no_title_tag(self):
        html = "<html><head></head></html>"
        assert parse_product_title(html) == ""

    def test_strips_suppliers_suffix(self):
        html = "<html><head><title>Widget | Suppliers &amp; Manufacturers</title></head></html>"
        assert parse_product_title(html) == "Widget"


class TestParseProductSpecs:
    def test_empty_html_returns_empty(self):
        assert parse_product_specs("<html><body></body></html>") == {}

    def test_ant_descriptions_table(self):
        html = """
        <html><body>
        <div id="Product" class="descriptions">
            <div class="title">Product Information</div>
            <div class="ant-descriptions ant-descriptions-small ant-descriptions-bordered">
                <div class="ant-descriptions-view"><table><tbody>
                    <tr class="ant-descriptions-row">
                        <th class="ant-descriptions-item-label ant-descriptions-item-colon">Model Number</th>
                        <td class="ant-descriptions-item-content">AL1001</td>
                        <th class="ant-descriptions-item-label ant-descriptions-item-colon">Brand Name</th>
                        <td class="ant-descriptions-item-content">OEM</td>
                    </tr>
                </tbody></table></div>
            </div>
        </div>
        </body></html>
        """
        specs = parse_product_specs(html)
        assert specs == {
            "Product Information": {
                "Model Number": "AL1001",
                "Brand Name": "OEM",
            }
        }

    def test_shipping_section(self):
        html = """
        <html><body>
        <div id="Shipping" class="descriptions">
            <div class="title">Shipping Details</div>
            <div class="ant-descriptions">
                <div class="ant-descriptions-view"><table><tbody>
                    <tr class="ant-descriptions-row">
                        <th class="ant-descriptions-item-label">FOB Port</th>
                        <td class="ant-descriptions-item-content">Guangzhou</td>
                        <th class="ant-descriptions-item-label">Lead Time</th>
                        <td class="ant-descriptions-item-content">40 days</td>
                    </tr>
                </tbody></table></div>
            </div>
        </div>
        </body></html>
        """
        specs = parse_product_specs(html)
        assert "Shipping Details" in specs
        assert specs["Shipping Details"]["FOB Port"] == "Guangzhou"

    def test_key_specifications_freetext(self):
        html = """
        <html><body>
        <div class="specifications">
            <h3 class="specifications-title">Key Specifications</h3>
            <div class="tpl_txt_editor">
                <p>Product name: 65 inch led smart tv</p>
                <p>System:Android 9.0</p>
                <p>aspect ratio:16:9</p>
            </div>
        </div>
        </body></html>
        """
        specs = parse_product_specs(html)
        assert "Key Specifications" in specs
        assert specs["Key Specifications"]["Product name"] == "65 inch led smart tv"
        assert specs["Key Specifications"]["System"] == "Android 9.0"
        # Colon in value preserved
        assert specs["Key Specifications"]["aspect ratio"] == "16:9"

    def test_multiple_sections_combined(self):
        html = """
        <html><body>
        <div id="Product" class="descriptions">
            <div class="title">Product Information</div>
            <div class="ant-descriptions"><div class="ant-descriptions-view"><table><tbody>
                <tr><th class="ant-descriptions-item-label">Model</th>
                    <td class="ant-descriptions-item-content">X100</td></tr>
            </tbody></table></div></div>
        </div>
        <div class="specifications">
            <div class="tpl_txt_editor"><p>Color: Black</p></div>
        </div>
        </body></html>
        """
        specs = parse_product_specs(html)
        assert "Product Information" in specs
        assert "Key Specifications" in specs


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
