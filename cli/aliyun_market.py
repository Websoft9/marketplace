from __future__ import annotations

import json
import urllib.parse
import urllib.request

from .alibaba_credentials import (
    explicit_credential_environment,
    missing_hint,
    read as read_credentials,
)
from .marketplace import PLATFORM_SUPPLIERS
from .store_models import InventoryError, Product


_SUPPLIER = PLATFORM_SUPPLIERS.get("aliyun") or {}
SUPPLIER_NAME = str(_SUPPLIER.get("name") or "")
STORE_ID = str(_SUPPLIER.get("id") or "")
# Public supplier UID advertised by the store, not an access credential.
SUPPLIER_UID = str(_SUPPLIER.get("uid") or "")
STORE_API = "https://market.aliyun.com/api/ajax/product/queryProducts.json"


def _require_supplier():
    missing = [
        key for key, value in (
            ("id", STORE_ID), ("name", SUPPLIER_NAME), ("uid", SUPPLIER_UID),
        ) if not value
    ]
    if missing:
        raise InventoryError(
            "config/marketplace.json must define aliyun.supplier."
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


def _api_page(client, request_factory, page, page_size, filters):
    request = request_factory()
    request.set_PageNumber(page)
    request.set_PageSize(page_size)
    request.set_Filters(filters)
    request.set_accept_format("json")
    return client.do_action_with_exception(request)


def _client(timeout: float = 30):
    """Use the same AK/STS environment for list and single-product requests."""
    credentials = read_credentials()
    access_key_id = credentials.access_key_id
    access_key_secret = credentials.access_key_secret
    security_token = credentials.security_token
    if not access_key_id or not access_key_secret:
        raise InventoryError(f"set {missing_hint()}")

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
        credential = StsTokenCredential(
            sts_access_key_id=access_key_id,
            sts_access_key_secret=access_key_secret,
            sts_token=security_token,
        )

    if credential is None:
        return AcsClient(access_key_id, access_key_secret, "cn-hangzhou", timeout=timeout)
    with explicit_credential_environment():
        return AcsClient(region_id="cn-hangzhou", credential=credential, timeout=timeout)


def fetch_aliyun_product(code: str, timeout: float = 30) -> dict[str, object]:
    """Fetch one public product's name and short description from Aliyun Market."""
    client = _client(timeout)
    try:
        from aliyunsdkmarket.request.v20151101.DescribeProductRequest import DescribeProductRequest
    except ImportError as error:
        raise RuntimeError("install the Aliyun SDK with: pip install '.[aliyun]'") from error

    request = DescribeProductRequest()
    request.set_Code(code)
    request.set_QueryDraft(False)
    try:
        response = client.do_action_with_exception(request)
    except Exception as error:
        # SDK exceptions can contain signed request details; only surface the
        # exception class to avoid accidentally printing credentials or tokens.
        raise RuntimeError(f"Aliyun Market API request failed ({type(error).__name__})") from None
    parsed = json.loads(response.decode("utf-8") if isinstance(response, bytes) else response)
    result = parsed.get("Result", parsed)
    if not isinstance(result, dict):
        raise RuntimeError("Aliyun returned an unexpected product response")
    return result


def _payload(response, label):
    try:
        parsed = json.loads(response)
    except (TypeError, ValueError, UnicodeDecodeError):
        raise InventoryError(f"{label} returned invalid JSON") from None
    if not isinstance(parsed, dict):
        raise InventoryError(f"{label} returned an unexpected response")
    return parsed


def _integer(value, label):
    if isinstance(value, bool) or not str(value).isdigit():
        raise InventoryError(f"Aliyun inventory has invalid {label}")
    return int(value)


def _visible_product_codes(timeout, progress):
    """Enumerate the public storefront; API results alone do not prove visibility."""
    visible = set()
    seen = set()
    expected_total = None
    page_size = 50
    for page in range(1, 1001):
        query = urllib.parse.urlencode({
            "supplierId": STORE_ID, "pageIndex": page, "pageSize": page_size,
        })
        request = urllib.request.Request(f"{STORE_API}?{query}", headers={
            "User-Agent": "Websoft9-marketplace/1.0",
            "Referer": f"https://market.aliyun.com/store/{STORE_ID}/index.html",
        })
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except Exception as error:
            raise InventoryError(
                f"Aliyun store query failed ({type(error).__name__}); visibility unverified"
            ) from None
        parsed = _payload(raw, "Aliyun store query")
        result = parsed.get("result")
        if parsed.get("success") is not True or not isinstance(result, dict):
            raise InventoryError("Aliyun store query failed; visibility unverified")
        total = _integer(result.get("totalRecords"), "store totalRecords")
        total_pages = _integer(result.get("totalPage"), "store totalPage")
        current_page = _integer(result.get("curPage"), "store curPage")
        rows = result.get("products")
        expected_pages = (total + page_size - 1) // page_size
        if (current_page != page or total_pages not in {expected_pages, max(1, expected_pages)}
                or not isinstance(rows, list)
                or len(rows) != min(page_size, max(0, total - (page - 1) * page_size))):
            raise InventoryError("Aliyun store pagination is incomplete or inconsistent")
        if expected_total is not None and total != expected_total:
            raise InventoryError("Aliyun store inventory changed during pagination; retry query")
        expected_total = total
        for row in rows:
            if not isinstance(row, dict):
                raise InventoryError("Aliyun store returned a malformed product")
            code = str(row.get("productcode") or "").strip()
            if not code or code in seen:
                raise InventoryError("Aliyun store returned a missing or repeated product code; inventory is incomplete")
            seen.add(code)
            if (str(row.get("shop_name") or "").strip() != SUPPLIER_NAME
                    or str(row.get("supplierid") or "") != STORE_ID
                    or row.get("delivery_method") != "镜像"):
                continue
            hidden = row.get("hiddenForCloudMarket")
            if hidden not in (0, "0", 1, "1") or isinstance(hidden, bool):
                raise InventoryError("Aliyun store product visibility is unverified")
            if hidden in (0, "0"):
                visible.add(code)
        if progress:
            progress(f"Aliyun store: page {page}/{max(1, total_pages)}, {len(visible)} visible images")
        if len(seen) == total:
            return visible
    raise InventoryError("Aliyun store pagination limit reached; inventory is incomplete")


def _list_api_products(timeout, progress):
    """Fetch the main dataset before consulting the storefront for visibility."""
    client = _client(timeout)
    try:
        from aliyunsdkmarket.request.v20151101.DescribeProductsRequest import DescribeProductsRequest
    except ImportError as error:
        raise InventoryError("install the Aliyun SDK with: pip install '.[aliyun]'") from error
    products = []
    seen = set()
    expected_total = None
    received = 0
    page_size = 50
    filters = [
        {"Key": "productType", "Value": "MIRROR"},
        {"Key": "aliyunUid", "Value": SUPPLIER_UID},
    ]
    for page in range(1, 1001):
        try:
            response = _api_page(client, DescribeProductsRequest, page, page_size, filters)
        except Exception as error:
            # The Market service can reject a filter key with HTTP 500 and
            # Invalid.Param even when it is still listed in the API metadata.
            # Fall back to the complete mirror inventory and keep the local
            # supplier verification below. Only do this before page 1 succeeds,
            # so pagination totals always come from one filter set.
            if (expected_total is None and len(filters) > 1
                    and _error_code(error) == "Invalid.Param"):
                filters = [{"Key": "productType", "Value": "MIRROR"}]
                if progress:
                    progress("Aliyun DescribeProducts rejected aliyunUid; retrying with productType only")
                try:
                    response = _api_page(client, DescribeProductsRequest, page, page_size, filters)
                except Exception as retry_error:
                    raise InventoryError(
                        f"Aliyun DescribeProducts failed ({_error_detail(retry_error)})"
                    ) from None
            else:
                raise InventoryError(
                    f"Aliyun DescribeProducts failed ({_error_detail(error)})"
                ) from None
        parsed = _payload(response, "Aliyun DescribeProducts")
        if parsed.get("Success") is False or parsed.get("Code"):
            raise InventoryError("Aliyun DescribeProducts returned an API error")
        result = parsed.get("Result", parsed)
        if not isinstance(result, dict):
            raise InventoryError("Aliyun DescribeProducts returned an unexpected result")
        total = _integer(result.get("TotalCount"), "TotalCount")
        if (_integer(result.get("PageNumber"), "PageNumber") != page
                or _integer(result.get("PageSize"), "PageSize") != page_size):
            raise InventoryError("Aliyun DescribeProducts pagination is inconsistent")
        if expected_total is not None and total != expected_total:
            raise InventoryError("Aliyun API inventory changed during pagination; retry query")
        expected_total = total
        items = result.get("ProductItems")
        rows = items.get("ProductItem") if isinstance(items, dict) else None
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list) or len(rows) != min(page_size, max(0, total - received)):
            raise InventoryError("Aliyun DescribeProducts returned incomplete product items")
        received += len(rows)
        for row in rows:
            if not isinstance(row, dict):
                raise InventoryError("Aliyun DescribeProducts returned a malformed product")
            code = str(row.get("Code") or "").strip()
            if not code or code in seen:
                raise InventoryError("Aliyun API returned a missing or repeated product code; inventory is incomplete")
            seen.add(code)
            if (str(row.get("SupplierName") or "").strip() != SUPPLIER_NAME
                    or str(row.get("SupplierId") or "") != STORE_ID
                    or row.get("DeliveryWay") != "镜像"):
                continue
            products.append(Product(
                code=code, entity_id=code, title=str(row.get("Name") or ""),
                summary=str(row.get("ShortDescription") or ""), visibility="StoreVisible",
            ))
        if progress:
            progress(f"Aliyun API: {received}/{total}, {len(products)} supplier images")
        if received == total:
            return products
    raise InventoryError("Aliyun API pagination limit reached; inventory is incomplete")


def list_aliyun_products(*, timeout=30, progress=None):
    """API code/title/summary are authoritative; storefront is only an image/visibility filter."""
    _require_supplier()
    products = _list_api_products(timeout, progress)
    visible = _visible_product_codes(timeout, progress)
    if visible - {product.code for product in products}:
        raise InventoryError("Aliyun visible store products are missing from the supplier API inventory; query incomplete")
    selected = [product for product in products if product.code in visible]
    if progress:
        progress(f"Aliyun inventory: {len(selected)} visible images confirmed")
    return selected
