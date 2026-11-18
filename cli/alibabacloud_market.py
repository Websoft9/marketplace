"""Read visible image listings from the public Websoft9 international store."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

from .store_models import InventoryError, Product

STORE_ID = "2116499"
SUPPLIER_NAME = "Websoft9"
STORE_URL = f"https://marketplace.alibabacloud.com/store/{STORE_ID}.html"
STORE_API = "https://marketplace.alibabacloud.com/api/ajax/newCommodityList/queryNewList.json"


def _integer(value):
    if isinstance(value, bool) or not str(value).isdigit():
        raise InventoryError("Alibaba Cloud store returned invalid pagination metadata")
    return int(value)


def list_products(*, timeout=30, progress=None):
    products = []
    seen = set()
    total_expected = None
    page_size = 20
    for page in range(1, 1001):
        query = urllib.parse.urlencode({
            "order": "NewListing", "curPage": page, "keywords": "",
            "pageSize": page_size, "publisherId": STORE_ID,
        })
        request = urllib.request.Request(f"{STORE_API}?{query}", headers={
            "User-Agent": "Websoft9-marketplace/1.0", "Referer": STORE_URL,
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                parsed = json.load(response)
        except Exception as error:
            raise InventoryError(f"Alibaba Cloud store query failed ({type(error).__name__})") from None
        if not isinstance(parsed, dict) or parsed.get("success") is not True:
            raise InventoryError("Alibaba Cloud store query returned an API error")
        result = parsed.get("result")
        if not isinstance(result, dict):
            raise InventoryError("Alibaba Cloud store query is missing result data")
        total = _integer(result.get("totalRecords"))
        total_pages = _integer(result.get("totalPage"))
        current_page = _integer(result.get("curPage"))
        rows = result.get("product")
        pages_expected = (total + page_size - 1) // page_size
        if (current_page != page or total_pages not in {pages_expected, max(1, pages_expected)}
                or not isinstance(rows, list)
                or len(rows) != min(page_size, max(0, total - (page - 1) * page_size))):
            raise InventoryError("Alibaba Cloud store pagination is incomplete or inconsistent")
        if total_expected is not None and total != total_expected:
            raise InventoryError("Alibaba Cloud store changed during pagination; retry query")
        total_expected = total
        for item in rows:
            row = item.get("productInfo") if isinstance(item, dict) else None
            if not isinstance(row, dict):
                raise InventoryError("Alibaba Cloud store returned malformed product data")
            code = str(row.get("code") or "").strip()
            if not code or code in seen:
                raise InventoryError("Alibaba Cloud store returned missing/repeated codes; inventory incomplete")
            seen.add(code)
            if row.get("companyName") != SUPPLIER_NAME or row.get("deliveryMethod") != "Image":
                continue
            shop = urllib.parse.urlsplit(str(row.get("shopUrl") or ""))
            if (shop.path != f"/store/{STORE_ID}.html"
                    or shop.netloc not in {"", "marketplace.alibabacloud.com"}):
                raise InventoryError("Alibaba Cloud product store ownership could not be verified")
            url = urllib.parse.urljoin(STORE_URL, str(row.get("detailUrl") or ""))
            parsed_url = urllib.parse.urlsplit(url)
            if (not row.get("detailUrl") or parsed_url.scheme != "https"
                    or parsed_url.netloc != "marketplace.alibabacloud.com"):
                raise InventoryError("Alibaba Cloud store returned an unverified product URL")
            products.append(Product(
                code=code, entity_id=code, title=str(row.get("title") or ""),
                summary=str(row.get("describe") or ""), visibility="StoreVisible", url=url,
            ))
        if progress:
            progress(f"Alibaba Cloud store: page {page}/{max(1, total_pages)}, {len(products)} visible images")
        if len(seen) == total:
            return products
    raise InventoryError("Alibaba Cloud store pagination limit reached; inventory incomplete")
