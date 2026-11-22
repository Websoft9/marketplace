"""Shared, read-only store inventory; never reads an app catalog or code matrix."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import alibabacloud_market, aliyun_market, aws_inventory, azure_market
from .marketplace import PLATFORM_URL_TEMPLATES
from .store_models import InventoryError, Product

PLATFORMS = ("aws", "aliyun", "alibabacloud", "azure", "azure-vmlab")
VISIBILITIES = ("public",)
PRODUCT_FACT_FIELDS = (
    "platform", "storeId", "provider", "code", "entityId", "title", "shortDescription",
    "sellerSku", "url", "productType", "visibility", "public", "visibilityEvidence",
)


def _validate_selection(platform, profile, visibility):
    if platform not in PLATFORMS:
        raise InventoryError(f"store queries are not implemented for {platform!r}")
    if profile is not None and platform != "aws":
        raise InventoryError("--profile is only supported for AWS")
    if visibility not in VISIBILITIES:
        raise InventoryError("store queries currently support only public image visibility")


def collect_products(platform, *, profile=None, visibility="public", progress=None):
    """Cloud collection behind the shared store service, with an explicit scope."""
    _validate_selection(platform, profile, visibility)
    if platform == "aws":
        return aws_inventory.list_aws_products(profile, public_only=True, progress=progress)
    if platform == "aliyun":
        return aliyun_market.list_aliyun_products(progress=progress)
    if platform == "azure":
        return azure_market.list_products(progress=progress)
    if platform == "azure-vmlab":
        return azure_market.list_products(platform=platform, progress=progress)
    return alibabacloud_market.list_products(progress=progress)


def _scope(platform):
    if platform in azure_market.PLATFORMS:
        supplier_id, supplier_name = azure_market.supplier(platform)
        return {
            "id": supplier_id, "provider": supplier_name,
            "url": "https://azuremarketplace.microsoft.com/en-us/marketplace/apps?search=" + supplier_id,
            "ownershipEvidence": "Authenticated Product Ingestion VM identities + public Catalog publisherId/offerId",
        }, [f"Product Ingestion {azure_market.API_VERSION} product + resource-tree targetType=live",
            f"{azure_market.CATALOG_API} api-version={azure_market.CATALOG_VERSION}"]
    if platform == "aws":
        return {
            # Seller profile IDs and AWS account IDs are not interchangeable.
            "id": "SELF", "provider": "Authenticated AWS seller", "url": None,
            "ownershipEvidence": "marketplace-catalog ListEntities, OwnershipType=SELF (default)",
        }, ["AWS Marketplace Catalog ListEntities", "AWS Marketplace Catalog BatchDescribeEntities"]
    if platform == "aliyun":
        return {
            "id": aliyun_market.STORE_ID, "provider": aliyun_market.SUPPLIER_NAME,
            "url": f"https://market.aliyun.com/store/{aliyun_market.STORE_ID}/index.html",
            "ownershipEvidence": "DescribeProducts SupplierId/SupplierName + storefront supplierid/shop_name",
        }, ["Market 2015-11-01 DescribeProducts", aliyun_market.STORE_API]
    return {
        "id": alibabacloud_market.STORE_ID, "provider": alibabacloud_market.SUPPLIER_NAME,
        "url": alibabacloud_market.STORE_URL,
        "ownershipEvidence": "DescribeProducts SupplierId/SupplierName + storefront companyName/shopUrl",
    }, ["Market 2015-11-01 DescribeProducts", alibabacloud_market.STORE_API]


def query_platform(platform, *, profile=None, visibility="public", progress=None):
    _validate_selection(platform, profile, visibility)
    started = time.monotonic()
    store, sources = _scope(platform)
    evidence = ("AWS Description.Visibility or AmiProductSummary.Visibility == Public" if platform == "aws"
                else azure_market.VISIBILITY_EVIDENCE if platform in azure_market.PLATFORMS
                else "DescribeProducts intersection with store images, hiddenForCloudMarket == 0"
                if platform == "aliyun"
                else "DescribeProducts intersection with store images, deliveryMethod == Image")
    result = {
        "platform": platform, "store": store,
        "queriedAt": datetime.now(timezone.utc).isoformat(),
        "scope": {"productType": "image", "visibility": visibility},
        "sources": sources, "complete": False, "count": 0, "products": [], "errors": [],
    }
    try:
        products = collect_products(platform, profile=profile, visibility=visibility, progress=progress)
        result["products"] = [{
            "platform": platform, "storeId": store["id"], "provider": store["provider"],
            "code": p.code, "entityId": p.entity_id, "title": p.title,
            "shortDescription": p.summary,
            "sellerSku": p.alternate_code or None,
            "url": p.url or (PLATFORM_URL_TEMPLATES[platform].format(code=p.code) if p.code else None),
            "productType": "image", "visibility": p.visibility, "public": True,
            "visibilityEvidence": evidence,
        } for p in products]
        result["count"] = len(products)
        result["complete"] = True
        inventory_products(result, platform)
    except Exception as error:
        # Only InventoryError messages are explicitly sanitized by our adapters.
        # Never stringify an arbitrary SDK exception, signed URL or response.
        result.update(complete=False, count=0, products=[])
        result["errors"] = [{
            "code": type(error).__name__,
            "message": str(error) if isinstance(error, InventoryError)
            else f"{platform} inventory query failed ({type(error).__name__})",
        }]
    result["elapsedSeconds"] = round(time.monotonic() - started, 2)
    return result


def load_inventory(source, platform):
    """Select one complete platform result from an explicit local store JSON snapshot."""
    try:
        document = json.loads(Path(source).read_bytes())
    except (OSError, ValueError):
        raise InventoryError("cannot read inventory snapshot as JSON") from None
    if (not isinstance(document, dict) or type(document.get("schemaVersion")) is not int
            or document["schemaVersion"] != 1 or not isinstance(document.get("results"), list)):
        raise InventoryError("inventory must be a schemaVersion 1 mk store products JSON result")
    selected = [r for r in document["results"] if isinstance(r, dict) and r.get("platform") == platform]
    if len(selected) != 1:
        raise InventoryError("inventory must contain exactly one result for the selected platform")
    inventory_products(selected[0], platform)
    return selected[0]


def get_inventory(platform, *, inventory_path=None, profile=None, visibility="public", progress=None):
    """Shared fact input for generation and offer resolution; never starts a CLI subprocess."""
    _validate_selection(platform, profile, visibility)
    if inventory_path is not None:
        if profile is not None:
            raise InventoryError("--profile cannot be combined with --inventory; snapshots are not re-queried")
        return load_inventory(inventory_path, platform)
    return query_platform(platform, profile=profile, visibility=visibility, progress=progress)


def inventory_provenance(inventory, inventory_path=None):
    result = {k: inventory[k] for k in (
        "platform", "store", "scope", "queriedAt", "sources", "count", "complete", "errors",
    ) if k in inventory}
    result.update(source="snapshot" if inventory_path is not None else "live",
                  sourcePath=str(inventory_path) if inventory_path is not None else None)
    return result


def inventory_products(inventory, platform):
    """Validate store facts before matrix generation, identically for live and snapshot data."""
    if (not isinstance(inventory, dict) or inventory.get("platform") != platform
            or inventory.get("complete") is not True or inventory.get("errors") != []):
        raise InventoryError("selected store inventory is incomplete or failed; nothing written")
    if inventory.get("scope") != {"productType": "image", "visibility": "public"}:
        raise InventoryError("inventory scope must be the complete public image inventory")
    try:
        timestamp = datetime.fromisoformat(inventory["queriedAt"].replace("Z", "+00:00"))
        if timestamp.utcoffset() is None:
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError):
        raise InventoryError("inventory must have a timezone-aware queriedAt timestamp") from None
    owner = inventory.get("store")
    if (not isinstance(owner, dict) or not all(isinstance(owner.get(k), str) and owner[k]
                                               for k in ("id", "provider", "ownershipEvidence"))
            or not isinstance(inventory.get("sources"), list) or not inventory["sources"]
            or not all(isinstance(s, str) and s for s in inventory["sources"])):
        raise InventoryError("inventory is missing store ownership or source evidence")
    expected_owner, _ = _scope(platform)
    if any(owner[k] != expected_owner[k] for k in ("id", "provider")):
        raise InventoryError("inventory store/provider does not match the supported platform scope")
    rows = inventory.get("products")
    if (not isinstance(rows, list) or type(inventory.get("count")) is not int
            or inventory["count"] != len(rows)):
        raise InventoryError("inventory product count is inconsistent")
    products = []
    entities = set()
    expected_visibility = "Public" if platform == "aws" else "StoreVisible"
    for row in rows:
        if (not isinstance(row, dict) or row.get("platform") != platform
                or row.get("storeId") != owner["id"] or row.get("provider") != owner["provider"]
                or row.get("productType") != "image" or row.get("public") is not True
                or row.get("visibility") != expected_visibility
                or not isinstance(row.get("visibilityEvidence"), str) or not row["visibilityEvidence"]
                or not all(isinstance(row.get(k), str) for k in ("code", "entityId", "title", "shortDescription"))
                or not row["entityId"] or row["entityId"] in entities
                or (row.get("sellerSku") is not None and not isinstance(row["sellerSku"], str))
                or (row.get("url") is not None and not isinstance(row["url"], str))):
            raise InventoryError("inventory contains malformed, nonpublic, duplicate or wrong-store product facts")
        entities.add(row["entityId"])
        if platform in azure_market.PLATFORMS and not azure_market.valid_identity(row["code"], row["entityId"], platform):
            raise InventoryError("Azure inventory contains wrong-publisher or malformed product identity")
        products.append(Product(
            code=row["code"], entity_id=row["entityId"], title=row["title"],
            summary=row["shortDescription"], visibility=row["visibility"],
            alternate_code=row.get("sellerSku") or "", url=row.get("url") or "",
        ))
    return products


def query_products(platforms, *, profile=None, visibility="public", progress=None):
    selected = list(dict.fromkeys(platforms))
    if not selected or any(p not in PLATFORMS for p in selected):
        raise InventoryError("select one or more implemented store platforms")
    if profile is not None and "aws" not in selected:
        raise InventoryError("--profile is only supported when AWS is selected")
    for platform in selected:
        _validate_selection(platform, profile if platform == "aws" else None, visibility)
    queried_at = datetime.now(timezone.utc).isoformat()
    # Each worker creates its own SDK session/client. Results keep requested order.
    with ThreadPoolExecutor(max_workers=len(selected)) as executor:
        jobs = [executor.submit(query_platform, p, profile=profile if p == "aws" else None,
                                visibility=visibility, progress=progress) for p in selected]
        results = [job.result() for job in jobs]
    return {
        "schemaVersion": 1, "queriedAt": queried_at,
        "complete": all(result["complete"] for result in results),
        "count": sum(result["count"] for result in results), "results": results,
    }
