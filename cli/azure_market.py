"""Read-only publisher-scoped Azure VM inventory: seller facts plus public evidence."""

from __future__ import annotations

import json
import errno
import http.client
import os
import random
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from .marketplace import PLATFORM_SUPPLIERS, PLATFORM_URL_TEMPLATES
from .store_models import InventoryError, Product


_SUPPLIER = PLATFORM_SUPPLIERS.get("azure") or {}
SUPPLIER_ID = str(_SUPPLIER.get("id") or "")
SUPPLIER_NAME = str(_SUPPLIER.get("name") or "")
PLATFORMS = ("azure", "azure-vmlab")
API_BASE = "https://graph.microsoft.com/rp/product-ingestion"
API_VERSION = "2026-11-01"
# Public storefront catalog interface, not the API-key-protected /products API.
CATALOG_API = "https://catalogapi.azure.com/offers"
CATALOG_VERSION = "2018-08-01-beta"
PRODUCT_TYPE = "azureVirtualMachine"
REQUEST_TIMEOUT = 60
MAX_ATTEMPTS = 3  # Initial request plus at most two retries; never retry the whole inventory.
MAX_RETRY_DELAY = 60
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
VISIBILITY_EVIDENCE = (
    "Product Ingestion live VM intersected with public Catalog offers; "
    "isPreview/isPrivate/isStopSell/isExcludedFromSearch == false; "
    "live submission generallyAvailable and visible public plan"
)
_SEGMENT = re.compile(r"[a-z0-9][a-z0-9_-]*")
_PRODUCT_ID = re.compile(r"product/[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")


def supplier(platform="azure"):
    if platform not in PLATFORMS:
        raise InventoryError("unsupported Azure publisher platform")
    if platform == "azure":
        return SUPPLIER_ID, SUPPLIER_NAME
    metadata = PLATFORM_SUPPLIERS.get(platform) or {}
    return str(metadata.get("id") or ""), str(metadata.get("name") or "")


def _require_supplier(platform="azure"):
    supplier_id, name = supplier(platform)
    if not _SEGMENT.fullmatch(supplier_id) or not name:
        raise InventoryError(f"config/marketplace.json must define {platform}.supplier.id/name")
    return supplier_id, name


def owns_code(code, platform="azure"):
    """Azure's matrix column is shared with VMLab; replace only this publisher."""
    supplier_id, _ = supplier(platform)
    return bool(supplier_id) and isinstance(code, str) and code.startswith(supplier_id + ".")


def valid_identity(code, entity_id, platform="azure"):
    return (isinstance(entity_id, str) and owns_code(code, platform) and bool(_SEGMENT.fullmatch(code.split(".", 1)[1]))
            and bool(_PRODUCT_ID.fullmatch(entity_id)))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward credentials or tokens to a redirected URL.
        return None


def _transient_network_error(error):
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(reason, ssl.SSLError):
        return False  # Never hide certificate or protocol failures behind retries.
    return (isinstance(reason, (TimeoutError, ConnectionError, http.client.IncompleteRead,
                                http.client.RemoteDisconnected))
            or isinstance(reason, socket.gaierror) and reason.errno == socket.EAI_AGAIN
            or isinstance(reason, OSError) and reason.errno in {
                errno.ETIMEDOUT, errno.ECONNRESET, errno.ECONNREFUSED,
                errno.ECONNABORTED, errno.EPIPE, errno.ENETUNREACH, errno.EHOSTUNREACH,
            })


def _retry_delay(attempt, retry_after=None):
    delay = 2 ** attempt + random.uniform(0, 1)
    if retry_after:
        try:
            text = retry_after.strip()
            # Retry-After allows seconds or an HTTP date; ignore invalid headers.
            seconds = (int(text) if text.isdecimal() and len(text) <= 10 else
                       (parsedate_to_datetime(text) - datetime.now(timezone.utc)).total_seconds())
            delay = max(delay, seconds)
        except (ValueError, TypeError, OverflowError, AttributeError):
            pass
    return min(MAX_RETRY_DELAY, delay)


def _request_json(url, *, timeout, token=None, data=None, context="Azure inventory", progress=None):
    headers = {"Accept": "application/json", "User-Agent": "websoft9-marketplace-cli"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, headers=headers, data=data)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        retry_after = None
        try:
            with urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
                result = json.load(response)
            break
        except urllib.error.HTTPError as error:
            reason = f"HTTP {error.code}"
            retryable = error.code in RETRYABLE_STATUS
            retry_after = error.headers.get("Retry-After") if error.headers else None
            error.close()
        except Exception as error:
            reason = type(error).__name__
            retryable = _transient_network_error(error)
        if not retryable or attempt == MAX_ATTEMPTS:
            raise InventoryError(
                f"{context}: request failed ({reason}; attempts={attempt}/{MAX_ATTEMPTS})"
            ) from None
        delay = _retry_delay(attempt, retry_after)
        if progress:
            # Contexts are authored stages/public codes; never log URLs, tokens or response bodies.
            progress(f"{context}: {reason}; retry {attempt + 1}/{MAX_ATTEMPTS} in {delay:.1f}s")
        time.sleep(delay)
    if not isinstance(result, dict) or "error" in result or result.get("errors"):
        raise InventoryError(f"{context}: invalid or unsuccessful JSON response")
    return result


def _access_token(timeout, platform="azure", progress=None):
    # No generic fallback for VMLab: an absent/partial set must never borrow Websoft9 credentials.
    prefix = "AZURE_VMLAB" if platform == "azure-vmlab" else "AZURE"
    keys = tuple(f"{prefix}_{suffix}" for suffix in ("TENANT_ID", "CLIENT_ID", "CLIENT_SECRET"))
    values = {key: os.environ.get(key, "").strip() for key in keys}
    missing = [key for key, value in values.items() if not value]
    if missing:
        raise InventoryError("Azure inventory requires " + ", ".join(missing))
    tenant, client, secret = (values[key] for key in keys)
    if not re.fullmatch(r"[A-Za-z0-9.-]+", tenant):
        raise InventoryError(f"{prefix}_TENANT_ID must be a tenant ID or domain")
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials", "client_id": client,
        "client_secret": secret,
        "scope": "https://graph.microsoft.com/.default",
    }).encode()
    response = _request_json(
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        timeout=timeout, data=data, context=f"{platform} authentication", progress=progress,
    )
    token = response.get("access_token")
    if not isinstance(token, str) or not token:
        raise InventoryError("Azure authentication returned no access token")
    return token


def _pages(base, params, rows_key, next_key, *, timeout, token=None, progress=None, context="Azure inventory"):
    url = base + "?" + urllib.parse.urlencode(params)
    seen = set()
    rows = []
    for page in range(1, 1001):
        if url in seen:
            raise InventoryError("Azure inventory pagination repeated a page; completeness unverified")
        seen.add(url)
        response = _request_json(url, timeout=timeout, token=token,
                                 context=f"{context} page {page}", progress=progress)
        items = response.get(rows_key)
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise InventoryError("Azure inventory returned malformed page items")
        rows.extend(items)
        if progress:
            progress(f"{context} page {page}: {len(items)} products")
        link = response.get(next_key)
        if link is None or link == "":
            return rows
        if not isinstance(link, str):
            raise InventoryError("Azure inventory returned a malformed next-page link")
        next_url = urllib.parse.urljoin(url, link)
        expected, actual = urllib.parse.urlsplit(base), urllib.parse.urlsplit(next_url)
        query = urllib.parse.parse_qs(actual.query)
        if (actual.scheme != "https" or actual.netloc != expected.netloc
                or actual.path != expected.path or actual.fragment
                or any(query.get(key) != [str(value)] for key, value in params.items())):
            raise InventoryError("Azure inventory next-page link changed host, path or query scope")
        url = next_url
    raise InventoryError("Azure inventory exceeded pagination limit; completeness unverified")


def _seller_products(token, timeout, progress, platform="azure"):
    supplier_id, _ = supplier(platform)
    rows = _pages(API_BASE + "/product", {"type": PRODUCT_TYPE, "$version": API_VERSION},
                  "value", "@nextLink", timeout=timeout, token=token, progress=progress,
                  context=f"{platform} seller")
    products = {}
    entity_ids = set()
    for row in rows:
        identity = row.get("identity")
        external_id = identity.get("externalId") if isinstance(identity, dict) else None
        entity_id = row.get("id")
        if (row.get("type") != PRODUCT_TYPE or not isinstance(external_id, str)
                or not _SEGMENT.fullmatch(external_id) or not isinstance(entity_id, str)
                or not _PRODUCT_ID.fullmatch(entity_id)):
            raise InventoryError("Azure seller returned malformed or non-VM product identity")
        code = supplier_id + "." + external_id
        if code in products or entity_id in entity_ids:
            raise InventoryError("Azure seller returned duplicate product identity")
        products[code] = row
        entity_ids.add(entity_id)
    return products


def _public_codes(timeout, progress, platform="azure"):
    supplier_id, supplier_name = supplier(platform)
    rows = _pages(CATALOG_API, {
        "api-version": CATALOG_VERSION, "$filter": f"publisherId eq '{supplier_id}'",
    }, "items", "nextPageLink", timeout=timeout, progress=progress,
       context=f"{platform} public catalog")
    codes = set()
    seen = set()
    for row in rows:
        code = row.get("id")
        offer_id = row.get("offerId")
        if (row.get("publisherId") != supplier_id or row.get("publisherDisplayName") != supplier_name
                or not isinstance(offer_id, str) or not _SEGMENT.fullmatch(offer_id)
                or code != supplier_id + "." + offer_id or code in seen):
            raise InventoryError("Azure public catalog returned wrong-publisher or malformed product identity")
        seen.add(code)
        if row.get("offerType") != "VirtualMachine":
            continue
        flags = ("isPreview", "isPrivate", "isStopSell", "isExcludedFromSearch")
        if any(type(row.get(flag)) is not bool for flag in flags):
            raise InventoryError("Azure public catalog visibility flags are missing or malformed")
        if all(row[flag] is False for flag in flags):
            codes.add(code)
    return codes


def _resources(tree, kind, entity_id):
    return [row for row in tree["resources"]
            if str(row.get("$schema", "")).startswith(f"https://schema.mp.microsoft.com/schema/{kind}/")
            and (row.get("id") == entity_id if kind == "product" else row.get("product") == entity_id)]


def _live_product(code, seller, token, timeout, platform="azure", progress=None):
    entity_id = seller["id"]
    tree = _request_json(API_BASE + "/resource-tree/" + entity_id + "?" + urllib.parse.urlencode({
        "targetType": "live", "$version": API_VERSION,
    }), timeout=timeout, token=token, context=f"{platform} live detail {code}", progress=progress)
    if (tree.get("root") != entity_id or tree.get("target") != {"targetType": "live"}
            or not isinstance(tree.get("resources"), list)
            or not all(isinstance(row, dict) for row in tree["resources"])):
        raise InventoryError("Azure resource tree is malformed or not the requested live product")
    products = _resources(tree, "product", entity_id)
    submissions = [row for row in _resources(tree, "submission", entity_id)
                   if row.get("target") == {"targetType": "live"}]
    if (len(products) != 1 or products[0].get("identity") != seller["identity"]
            or products[0].get("type") != PRODUCT_TYPE or len(submissions) != 1):
        raise InventoryError("Azure live resource tree has missing or conflicting product/submission identity")
    submission = submissions[0]
    if submission.get("lifecycleState") in ("deprecated", "deleted"):
        return None
    if (submission.get("status") != "completed" or submission.get("result") != "succeeded"
            or submission.get("lifecycleState") != "generallyAvailable"):
        raise InventoryError("Azure live submission status is unknown; public visibility unverified")
    plan_rows = _resources(tree, "plan", entity_id)
    if any(not isinstance(row.get("id"), str) or not row["id"].startswith("plan/") for row in plan_rows):
        raise InventoryError("Azure live product has malformed plan identity")
    plans = {row["id"]: row for row in plan_rows}
    availability = _resources(tree, "price-and-availability-plan", entity_id)
    if not plans or not availability or len(plans) != len(plan_rows):
        raise InventoryError("Azure live product is missing plan visibility evidence")
    public_plan = False
    seen_plans = set()
    for row in availability:
        if (not isinstance(row.get("plan"), str) or row["plan"] not in plans
                or row["plan"] in seen_plans or row.get("audience") not in ("public", "private")
                or row.get("visibility") not in ("visible", "hidden")):
            raise InventoryError("Azure live plan has malformed or unknown visibility evidence")
        seen_plans.add(row["plan"])
        plan = plans[row["plan"]]
        lifecycle = plan.get("lifecycleState", "generallyAvailable")
        if lifecycle not in ("generallyAvailable", "deprecated", "deleted"):
            raise InventoryError("Azure live plan has unknown lifecycle state")
        public_plan |= (row["audience"] == "public" and row["visibility"] == "visible"
                        and lifecycle == "generallyAvailable")
    if seen_plans != plans.keys():
        raise InventoryError("Azure live product is missing plan visibility evidence")
    if not public_plan:
        return None
    listings = [row for row in _resources(tree, "listing", entity_id)
                if row.get("kind") == "azureVM" and row.get("languageId") == "en-us"]
    if (len(listings) != 1 or not all(isinstance(listings[0].get(key), str)
                                      for key in ("title", "shortDescription"))):
        raise InventoryError("Azure live product is missing an unambiguous English VM listing")
    return Product(code=code, entity_id=entity_id, title=listings[0]["title"],
                   summary=listings[0]["shortDescription"], visibility="StoreVisible",
                    url=PLATFORM_URL_TEMPLATES[platform].format(code=code))


def list_products(*, platform="azure", timeout=REQUEST_TIMEOUT, progress=None):
    """Enumerate one publisher using its own immutable token and query scope."""
    _require_supplier(platform)
    token = _access_token(timeout, platform, progress)
    seller = _seller_products(token, timeout, progress, platform)
    public = _public_codes(timeout, progress, platform)
    if public - seller.keys():
        raise InventoryError("Azure public VM products are missing from the authenticated seller inventory")
    # Only public candidates need resource-tree reads. Never expose contacts, audiences or images.
    with ThreadPoolExecutor(max_workers=4) as executor:
        codes = sorted(public)
        jobs = [executor.submit(_live_product, code, seller[code], token, timeout, platform, progress) for code in codes]
        products = []
        for index, (code, job) in enumerate(zip(codes, jobs), 1):
            try:
                products.append(job.result())
            except InventoryError as error:
                context = f"{platform} live detail {code}"
                raise InventoryError(str(error) if str(error).startswith(context) else f"{context}: {error}") from None
            if progress:
                progress(f"{platform} live details: {index}/{len(jobs)}")
    result = [product for product in products if product is not None]
    if progress:
        progress(f"{platform}: {len(seller)} seller VMs, {len(public)} public candidates, {len(result)} live public VMs")
    return result
