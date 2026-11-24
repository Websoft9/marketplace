"""Read-only Google Cloud public Marketplace browse interface (not seller OpenAPI).

No account credentials or cookies are sent. The public console bootstrap key is
read afresh, kept in memory and never included in output or error messages.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from .marketplace import PLATFORM_SUPPLIERS, PLATFORM_URL_TEMPLATES
from .store_models import InventoryError, Product


STORE_API = (
    "https://cloudconsole-pa.clients6.google.com/v3/entityServices/"
    "MarketplaceEntityService/entities/MARKETPLACE_BROWSE_PRODUCTS:get"
)
RESPONSE_TYPE = "type.googleapis.com/google.internal.cloud.console.clientapi.marketplace.BrowseProductsResponse"
PAGE_SIZE = 40
MAX_PAGES = 1000
VISIBILITY_EVIDENCE = (
    "Unauthenticated public console browse, LAUNCHER/isPrivateCatalog=false; "
    "selected partner facet, subtitle and publisher namespace verified; productType=Virtual machines "
    "(observed storefront evidence, not seller publication status)"
)


def supplier():
    metadata = PLATFORM_SUPPLIERS.get("googlecloud") or {}
    store_id, name = metadata.get("id"), metadata.get("name")
    if (not isinstance(store_id, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", store_id)
            or not isinstance(name, str) or not name.strip()):
        raise InventoryError("config/marketplace.json must define googlecloud.supplier.id/name")
    return store_id, name


def store_url():
    return "https://console.cloud.google.com/marketplace/browse?" + urllib.parse.urlencode(
        {"filter": "partner:" + supplier()[1]})


def valid_identity(code, entity_id):
    return (isinstance(code, str) and bool(re.fullmatch(
        re.escape(supplier()[0]) + r"/[a-z0-9][a-z0-9-]*", code))
        and isinstance(entity_id, str)
        and bool(re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", entity_id)))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _read(request, timeout):
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        raise InventoryError(f"Google Cloud public store query failed (HTTP {error.code})") from None
    except Exception as error:
        raise InventoryError(f"Google Cloud public store query failed ({type(error).__name__})") from None


def _bootstrap(timeout):
    html = _read(urllib.request.Request(store_url(), headers={
        "User-Agent": "websoft9-marketplace-cli", "Accept": "text/html",
    }), timeout)
    # This is public frontend configuration, not a user-provided API credential.
    matches = re.findall(r'"LjgHrc"\s*:\s*"([^"\\]+)"', html)
    if len(matches) != 1 or not matches[0]:
        raise InventoryError("Google Cloud public console bootstrap schema changed")
    return matches[0]


def _request_page(token, key, timeout):
    payload = {"entityRequest": {"entityName": "MARKETPLACE_BROWSE_PRODUCTS", "requestPayload": {
        "@type": "type.googleapis.com/google.internal.cloud.console.clientapi.marketplace.BrowseProductsRequest",
        "parameters": {"locale": "en_US", "solutionScopeId": "LAUNCHER",
                       "selectedFacetValues": [{"facetId": "partner", "facetValueId": supplier()[1]}],
                       "query": "", "pageToken": token, "isPrivateCatalog": False},
    }}}
    text = _read(urllib.request.Request(STORE_API, data=json.dumps(payload).encode(), headers={
        "Content-Type": "application/json", "Accept": "application/json",
        "Referer": "https://console.cloud.google.com/", "X-Goog-Api-Key": key,
        "User-Agent": "websoft9-marketplace-cli",
    }), timeout)
    try:
        data = json.loads(text)
        if not isinstance(data, list) or len(data) != 1:
            raise ValueError
        result = data[0]["successfulResult"]["resultData"]
        if result["@type"] != RESPONSE_TYPE:
            raise ValueError
        return result["marketplaceBrowseProducts"]
    except (ValueError, KeyError, TypeError):
        raise InventoryError("Google Cloud public store returned an unsuccessful or unknown response") from None


def list_products(*, timeout=30, progress=None):
    """Enumerate all public partner cards, then return only verified VM listings."""
    _, name = supplier()
    key = _bootstrap(timeout)
    token, total = "", None
    tokens, codes, entities = set(), set(), set()
    products = []
    for page in range(MAX_PAGES):
        data = _request_page(token, key, timeout)
        if not isinstance(data, dict):
            raise InventoryError("Google Cloud public store returned malformed browse data")
        facets = data.get("selectedLabeledFacetValues")
        if (not isinstance(facets, list) or len(facets) != 1 or not isinstance(facets[0], dict)
                or facets[0].get("facetBinding") != {"facetId": "partner", "facetValueId": name}
                or facets[0].get("displayName") != name):
            raise InventoryError("Google Cloud public store partner scope unverified")
        count, shelfs = data.get("totalCount"), data.get("shelfs")
        if (type(count) is not int or count < 0 or not isinstance(shelfs, list)
                or len(shelfs) != 1 or not isinstance(shelfs[0], dict)
                or shelfs[0].get("shelfType") != "SEARCH_RESULT"
                or not isinstance(shelfs[0].get("cards"), list)):
            raise InventoryError("Google Cloud public store returned malformed search pagination")
        if total is None:
            total = count
        if total != count:
            raise InventoryError("Google Cloud store total changed during pagination")
        rows = shelfs[0]["cards"]
        if len(rows) != min(PAGE_SIZE, max(0, total - len(codes))):
            raise InventoryError("Google Cloud public store page is truncated or inconsistent")
        for row in rows:
            if not isinstance(row, dict):
                raise InventoryError("Google Cloud public store returned malformed product")
            code, entity = row.get("productDisplayName"), row.get("productId")
            if row.get("subtitle") != name or not valid_identity(code, entity):
                raise InventoryError("Google Cloud public store returned wrong-owner or malformed product identity")
            if code in codes or entity in entities:
                raise InventoryError("Google Cloud public store returned duplicate product identities")
            codes.add(code)
            entities.add(entity)
            kind = row.get("productType")
            if not isinstance(kind, str) or not kind:
                raise InventoryError("Google Cloud public store product type unverified")
            if kind != "Virtual machines":
                continue
            if (row.get("familyType") != "PackagedDeployment"
                    or not all(isinstance(row.get(k), str) and row[k] for k in ("title", "description"))):
                raise InventoryError("Google Cloud VM listing fields are missing or malformed")
            products.append(Product(code=code, entity_id=entity, title=row["title"],
                                    summary=row["description"], visibility="StoreVisible",
                                    url=PLATFORM_URL_TEMPLATES["googlecloud"].format(code=code)))
        token = data.get("nextPageToken", "")
        if not isinstance(token, str) or token in tokens or (len(codes) == total) != (not token):
            raise InventoryError("Google Cloud public store continuation is missing, repeated or inconsistent")
        if progress:
            progress(f"googlecloud page {page + 1}: {len(codes)}/{total} products, {len(products)} public images")
        if not token:
            return sorted(products, key=lambda product: product.code)
        tokens.add(token)
    raise InventoryError("Google Cloud public store exceeded pagination limit; completeness unverified")
