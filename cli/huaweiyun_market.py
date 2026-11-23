"""Huawei Cloud public seller inventories, not authenticated seller APIs.

The source is the same guest search used by the official seller page. Never
send AK/SK, security tokens or cookies to this public website interface.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from html.parser import HTMLParser

from .marketplace import PLATFORM_SUPPLIERS, PLATFORM_URL_TEMPLATES
from .store_models import InventoryError, Product


_SUPPLIER = PLATFORM_SUPPLIERS.get("huaweiyun") or {}
STORE_ID = str(_SUPPLIER.get("id") or "")
SUPPLIER_NAME = str(_SUPPLIER.get("name") or "")
STORE_API = (
    "https://mkpdata.huaweicloud.com/api/marketplace/user/api/"
    "mkp-web-guest/global/rest/mkp/v1/mkpsearchengineservice/search"
)
PLATFORMS = ("huaweiyun", "huaweicloud")
INTERNATIONAL_API = (
    "https://searchrec.marketplace-intl.huaweicloud.com"
    "/rest/mkp/v1/mkpsearchengineservice/search"
)
PAGE_SIZE = 10
MAX_PAGES = 1000
VISIBILITY_EVIDENCE = (
    "Official China guest seller search, flow_id=101000; verified seller_id/"
    "corporation_name/corporation_url; delivery_code=vm.image, "
    "content_status=1 and hidden=0 (observed public storefront flags)"
)
_CONTENT_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")


def supplier(platform="huaweiyun"):
    if platform not in PLATFORMS:
        raise InventoryError("unsupported Huawei public store platform")
    if platform == "huaweiyun":
        return STORE_ID, SUPPLIER_NAME
    metadata = PLATFORM_SUPPLIERS.get(platform) or {}
    return str(metadata.get("id") or ""), str(metadata.get("name") or "")


def source(platform="huaweiyun"):
    supplier(platform)  # Reject unknown platforms, never default to another site's endpoint.
    if platform == "huaweiyun":
        return STORE_API, "101000", "", "hcontent"
    return INTERNATIONAL_API, "101002", "/intl", "content_abstract"


def visibility_evidence(platform="huaweiyun"):
    if platform == "huaweiyun":
        return VISIBILITY_EVIDENCE
    _, flow, _, _ = source(platform)
    return VISIBILITY_EVIDENCE.replace("China", "International").replace("101000", flow)


def _require_supplier(platform="huaweiyun"):
    store_id, name = supplier(platform)
    if not re.fullmatch(r"[0-9a-f]{32}", store_id) or not name:
        raise InventoryError(f"config/marketplace.json must define {platform}.supplier.id/name")


def valid_identity(code, entity_id):
    # This is the public content UUID, NOT an OFFI offering ID, SKU or image ID.
    return isinstance(code, str) and bool(_CONTENT_ID.fullmatch(code)) and entity_id == code


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _request_page(offset, timeout, platform="huaweiyun"):
    endpoint, flow, _, _ = source(platform)
    store_id, _ = supplier(platform)
    payload = {"flow_id": flow, "filter": {"seller_id": store_id},
               "offset": offset, "limit": PAGE_SIZE}
    headers = {
        "Content-Type": "application/json", "Accept": "application/json",
        "User-Agent": "websoft9-marketplace-cli",
    }
    if platform == "huaweiyun":
        payload["sort"] = "onshelfDesc"
    else:
        # Match the international seller page's actual request; flow selects its English data.
        headers["x-language"] = "zh-cn"
    request = urllib.request.Request(endpoint, data=json.dumps(payload).encode(), headers=headers)
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            data = json.load(response)
    except urllib.error.HTTPError as error:
        raise InventoryError(f"{platform} public store query failed (HTTP {error.code})") from None
    except Exception as error:
        raise InventoryError(f"{platform} public store query failed ({type(error).__name__})") from None
    if not isinstance(data, dict) or data.get("error_code") != "00000000":
        raise InventoryError(f"{platform} public store returned an unsuccessful or unknown response")
    return data


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def _summary(value):
    # hcontent is the storefront short description, potentially search-highlighted HTML.
    parser = _PlainText()
    parser.feed(value)
    parser.close()
    return "".join(parser.parts)


def _product(row, platform="huaweiyun"):
    store_id, name = supplier(platform)
    _, _, prefix, summary_field = source(platform)
    code = row.get("id")
    if (row.get("seller_id") != store_id or row.get("corporation_name") != name
            or row.get("corporation_url") != prefix + "/seller/" + store_id
            or not valid_identity(code, code) or row.get("url") != prefix + "/contents/" + code):
        raise InventoryError(f"{platform} store returned wrong-owner or malformed product identity")
    delivery = row.get("delivery_code")
    if not isinstance(delivery, str) or not delivery:
        raise InventoryError(f"{platform} store is missing product delivery evidence")
    if delivery != "vm.image":
        return None
    if (type(row.get("hidden")) is not int or row["hidden"] not in (0, 1)
            or type(row.get("content_status")) is not int):
        raise InventoryError(f"{platform} image visibility evidence is missing or malformed")
    if row["hidden"] == 1:
        return None
    # Only the observed public state is recognized, not a guessed status enum.
    if row["content_status"] != 1:
        raise InventoryError(f"{platform} image has an unknown storefront status; public scope unverified")
    if not all(isinstance(row.get(key), str) for key in ("title", summary_field)):
        raise InventoryError(f"{platform} image title/short description is missing or malformed")
    summary = _summary(row[summary_field]) if platform == "huaweiyun" else row[summary_field]
    return Product(code=code, entity_id=code, title=row["title"], summary=summary,
                   visibility="StoreVisible", url=PLATFORM_URL_TEMPLATES[platform].format(code=code))


def list_products(*, platform="huaweiyun", timeout=30, progress=None):
    """Collect every public seller page before returning normalized image facts."""
    _require_supplier(platform)
    total = None
    seen = set()
    products = []
    for page in range(MAX_PAGES):
        offset = page * PAGE_SIZE
        data = (_request_page(offset, timeout) if platform == "huaweiyun"
                else _request_page(offset, timeout, platform))
        count = data.get("total")
        pagination = data.get("pagination")
        rows = pagination.get("items") if isinstance(pagination, dict) else None
        if (type(count) is not int or count < 0 or not isinstance(rows, list)
                or not all(isinstance(row, dict) for row in rows)):
            raise InventoryError(f"{platform} store returned malformed pagination")
        if total is None:
            total = count
        if total != count:
            raise InventoryError(f"{platform} store total changed during pagination; retry the query")
        if len(rows) != min(PAGE_SIZE, max(0, total - offset)):
            raise InventoryError(f"{platform} store page is incomplete or inconsistent with its total")
        for row in rows:
            product = _product(row, platform)
            if row["id"] in seen:
                raise InventoryError(f"{platform} store returned duplicate or repeated product identities")
            seen.add(row["id"])
            if product is not None:
                products.append(product)
        if progress:
            progress(f"{platform} page {page + 1}: {len(seen)}/{total} store products, {len(products)} public images")
        if len(seen) == total:
            return sorted(products, key=lambda product: product.code)
    raise InventoryError(f"{platform} store exceeded pagination limit; completeness unverified")
