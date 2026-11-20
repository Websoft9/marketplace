"""Read public image listings from Alibaba Cloud International.

The authenticated Market ``DescribeProducts`` API is authoritative for product
codes, titles and summaries. The public Websoft9 storefront is used only to
verify publisher ownership, image delivery and public visibility.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

from .alibaba_credentials import (
    explicit_credential_environment,
    missing_hint_international,
    read_international,
)
from .marketplace import PLATFORM_SUPPLIERS
from .store_models import InventoryError, Product

_SUPPLIER = PLATFORM_SUPPLIERS.get("alibabacloud") or {}
SUPPLIER_NAME = str(_SUPPLIER.get("name") or "")
STORE_ID = str(_SUPPLIER.get("id") or "")
# International Market endpoints resolve from an overseas region to
# market.ap-southeast-1.aliyuncs.com. China uses a separate endpoint/account.
REGION_ID = "ap-southeast-1"
STORE_URL = f"https://marketplace.alibabacloud.com/store/{STORE_ID}.html"
STORE_API = "https://marketplace.alibabacloud.com/api/ajax/newCommodityList/queryNewList.json"
PAGE_SIZE = 50
STORE_PAGE_SIZE = 20


def _require_supplier():
    missing = [
        key for key, value in (("id", STORE_ID), ("name", SUPPLIER_NAME))
        if not value
    ]
    if missing:
        raise InventoryError(
            "config/marketplace.json must define alibabacloud.supplier."
            + ", .".join(missing)
        )


def _error_code(error):
    getter = getattr(error, "get_error_code", None)
    if callable(getter):
        try:
            return getter()
        except Exception:
            return None
    return None


def _error_detail(error):
    """Describe an SDK error without exposing signed request details."""
    parts = [type(error).__name__]
    status_getter = getattr(error, "get_http_status", None)
    if callable(status_getter):
        try:
            status = status_getter()
        except Exception:
            status = None
        if status not in (None, ""):
            parts.append(f"HTTP {status}")
    code = _error_code(error)
    if code:
        parts.append(str(code))
    return ", ".join(parts)


def _retryable(error):
    """Return whether an SDK error is a transient transport/server failure."""
    name = type(error).__name__
    code = str(_error_code(error) or "")
    status_getter = getattr(error, "get_http_status", None)
    status = None
    if callable(status_getter):
        try:
            status = status_getter()
        except Exception:
            status = None
    if name == "ClientException":
        return code in {"SDK.HttpError", "SDK.ServerUnreachable", "SDK.UnknownError"}
    if name == "ServerException":
        if code in {
            "Forbidden.RAM", "InvalidAccessKeyId", "InvalidSecurityToken",
            "SignatureDoesNotMatch", "Invalid.Param",
        }:
            return False
        return status is None or status in {500, 502, 503, 504}
    return False


def _client(timeout: float = 30):
    credentials = read_international()
    access_key_id = credentials.access_key_id
    access_key_secret = credentials.access_key_secret
    security_token = credentials.security_token
    if not access_key_id or not access_key_secret:
        raise InventoryError(f"set {missing_hint_international()}")

    try:
        from aliyunsdkcore.client import AcsClient
    except ImportError as error:
        raise InventoryError("install the Aliyun SDK with: pip install '.[aliyun]'") from error

    credential = None
    if security_token:
        try:
            from aliyunsdkcore.auth.credentials import StsTokenCredential
        except ImportError as error:
            raise InventoryError("installed Aliyun SDK does not support STS credentials") from error
        credential = StsTokenCredential(access_key_id, access_key_secret, security_token)

    if credential is None:
        return AcsClient(access_key_id, access_key_secret, REGION_ID, timeout=timeout)
    with explicit_credential_environment():
        return AcsClient(region_id=REGION_ID, credential=credential, timeout=timeout)


def _integer(value, label):
    if isinstance(value, bool) or not str(value).isdigit():
        raise InventoryError(f"Alibaba Cloud inventory has invalid {label}")
    return int(value)


def _payload(response, label):
    try:
        parsed = json.loads(response)
    except (TypeError, ValueError, UnicodeDecodeError):
        raise InventoryError(f"{label} returned invalid JSON") from None
    if not isinstance(parsed, dict):
        raise InventoryError(f"{label} returned an unexpected response")
    return parsed


def _api_page(client, request_factory, page):
    request = request_factory()
    request.set_PageNumber(page)
    request.set_PageSize(PAGE_SIZE)
    # The international endpoint rejects aliyunUid filters. SearchTerm=Websoft9
    # returns the complete supplier set; SupplierId/SupplierName are still
    # verified locally before any product is accepted.
    request.set_SearchTerm(SUPPLIER_NAME)
    request.set_Filters([{"Key": "productType", "Value": "MIRROR"}])
    request.set_accept_format("json")
    return client.do_action_with_exception(request)


def _list_api_products(timeout, progress):
    """Enumerate the complete mirror inventory, retrying a moving total."""
    try:
        from aliyunsdkmarket.request.v20151101.DescribeProductsRequest import (
            DescribeProductsRequest,
        )
    except ImportError as error:
        raise InventoryError("install the Aliyun SDK with: pip install '.[aliyun]'") from error

    client = _client(timeout)
    for attempt in range(1, 4):
        products = []
        seen = set()
        expected_total = None
        received = 0
        stable = True
        page = 1
        while True:
            for page_attempt in range(1, 4):
                try:
                    response = _api_page(client, DescribeProductsRequest, page)
                    break
                except Exception as error:
                    if page_attempt == 3 or not _retryable(error):
                        raise InventoryError(
                            f"Alibaba Cloud DescribeProducts failed ({_error_detail(error)})"
                        ) from None
                    if progress:
                        progress(
                            f"Alibaba Cloud DescribeProducts transient error on page {page}; "
                            f"retrying ({page_attempt}/3)"
                        )
                    time.sleep(page_attempt)
            parsed = _payload(response, "Alibaba Cloud DescribeProducts")
            if parsed.get("Success") is False or parsed.get("Code"):
                raise InventoryError("Alibaba Cloud DescribeProducts returned an API error")
            result = parsed.get("Result", parsed)
            if not isinstance(result, dict):
                raise InventoryError("Alibaba Cloud DescribeProducts returned an unexpected result")
            total = _integer(result.get("TotalCount"), "TotalCount")
            if (_integer(result.get("PageNumber"), "PageNumber") != page
                    or _integer(result.get("PageSize"), "PageSize") != PAGE_SIZE):
                raise InventoryError("Alibaba Cloud DescribeProducts pagination is inconsistent")
            if expected_total is None:
                expected_total = total
            elif total != expected_total:
                if progress:
                    progress(
                        f"Alibaba Cloud API total changed ({expected_total} -> {total}); "
                        f"retrying inventory ({attempt}/3)"
                    )
                stable = False
                break
            items = result.get("ProductItems")
            rows = items.get("ProductItem") if isinstance(items, dict) else None
            if isinstance(rows, dict):
                rows = [rows]
            if total == 0 and rows is None:
                rows = []
            if (not isinstance(rows, list)
                    or len(rows) != min(PAGE_SIZE, max(0, total - received))):
                raise InventoryError("Alibaba Cloud DescribeProducts returned incomplete product items")
            received += len(rows)
            for row in rows:
                if not isinstance(row, dict):
                    raise InventoryError("Alibaba Cloud DescribeProducts returned a malformed product")
                code = str(row.get("Code") or "").strip()
                if not code or code in seen:
                    raise InventoryError(
                        "Alibaba Cloud API returned a missing or repeated product code; "
                        "inventory is incomplete"
                    )
                seen.add(code)
                if (str(row.get("SupplierName") or "").strip() != SUPPLIER_NAME
                        or str(row.get("SupplierId") or "") != STORE_ID
                        or str(row.get("DeliveryWay") or "").strip() != "Image"):
                    continue
                products.append(Product(
                    code=code, entity_id=code,
                    title=str(row.get("Name") or ""),
                    summary=str(row.get("ShortDescription") or ""),
                    visibility="StoreVisible",
                ))
            if progress:
                progress(
                    f"Alibaba Cloud API: {received}/{total}, {len(products)} supplier images"
                )
            if received == total:
                break
            page += 1
            if page > 1000:
                raise InventoryError("Alibaba Cloud API pagination limit reached; inventory is incomplete")
        if stable:
            return products
    raise InventoryError("Alibaba Cloud API inventory changed during pagination; retry query")


def _visible_products(timeout, progress):
    """Return public store image code -> verified product URL."""
    visible = {}
    seen = set()
    expected_total = None
    for page in range(1, 1001):
        query = urllib.parse.urlencode({
            "order": "NewListing", "curPage": page, "keywords": "",
            "pageSize": STORE_PAGE_SIZE, "publisherId": STORE_ID,
        })
        request = urllib.request.Request(f"{STORE_API}?{query}", headers={
            "User-Agent": "Websoft9-marketplace/1.0", "Referer": STORE_URL,
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except Exception as error:
            raise InventoryError(
                f"Alibaba Cloud store query failed ({type(error).__name__})"
            ) from None
        parsed = _payload(raw, "Alibaba Cloud store query")
        if parsed.get("success") is not True:
            raise InventoryError("Alibaba Cloud store query returned an API error")
        result = parsed.get("result")
        if not isinstance(result, dict):
            raise InventoryError("Alibaba Cloud store query is missing result data")
        total = _integer(result.get("totalRecords"), "totalRecords")
        total_pages = _integer(result.get("totalPage"), "totalPage")
        current_page = _integer(result.get("curPage"), "curPage")
        rows = result.get("product")
        if total == 0 and rows is None:
            rows = []
        pages_expected = (total + STORE_PAGE_SIZE - 1) // STORE_PAGE_SIZE
        if (current_page != page or total_pages not in {pages_expected, max(1, pages_expected)}
                or not isinstance(rows, list)
                or len(rows) != min(STORE_PAGE_SIZE, max(0, total - (page - 1) * STORE_PAGE_SIZE))):
            raise InventoryError("Alibaba Cloud store pagination is incomplete or inconsistent")
        if expected_total is not None and total != expected_total:
            raise InventoryError("Alibaba Cloud store changed during pagination; retry query")
        expected_total = total
        for item in rows:
            row = item.get("productInfo") if isinstance(item, dict) else None
            if not isinstance(row, dict):
                raise InventoryError("Alibaba Cloud store returned malformed product data")
            code = str(row.get("code") or "").strip()
            if not code or code in seen:
                raise InventoryError(
                    "Alibaba Cloud store returned missing/repeated codes; inventory incomplete"
                )
            seen.add(code)
            if (str(row.get("companyName") or "").strip() != SUPPLIER_NAME
                    or str(row.get("deliveryMethod") or "").strip() != "Image"):
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
            visible[code] = url
        if progress:
            progress(
                f"Alibaba Cloud store: page {page}/{max(1, total_pages)}, "
                f"{len(visible)} visible images"
            )
        if len(seen) == total:
            return visible
    raise InventoryError("Alibaba Cloud store pagination limit reached; inventory is incomplete")


def list_products(*, timeout=30, progress=None):
    """Return API facts for every publicly visible Websoft9 image product."""
    _require_supplier()
    products = _list_api_products(timeout, progress)
    visible = _visible_products(timeout, progress)
    missing = set(visible) - {product.code for product in products}
    if missing:
        raise InventoryError(
            "Alibaba Cloud visible store products are missing from the supplier API inventory; "
            "query incomplete"
        )
    selected = []
    for product in products:
        url = visible.get(product.code)
        if url:
            product.url = url
            selected.append(product)
    if progress:
        progress(f"Alibaba Cloud inventory: {len(selected)} visible images confirmed")
    return selected
