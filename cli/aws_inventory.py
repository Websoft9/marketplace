"""Read the authenticated seller's AMI inventory without an application catalog."""

from __future__ import annotations

import json
from typing import Any

from .aws_marketplace import CATALOG, aws_call, aws_client
from .store_models import InventoryError, Product


def _product(entity_id: str, entity: dict, summary: dict) -> Product:
    detail = entity.get("DetailsDocument")
    if detail is None:
        detail = json.loads(entity.get("Details") or "{}")
    description = detail.get("Description") or {}
    sku = str(description.get("Sku") or "")
    code = str(description.get("ProductCode") or "")
    return Product(
        code=code, entity_id=entity_id,
        title=str(description.get("ProductTitle") or summary.get("ProductTitle") or ""),
        summary=str(description.get("ShortDescription") or ""),
        visibility=str(description.get("Visibility") or summary.get("Visibility") or ""),
        alternate_code=sku if sku and sku != code else "",
    )


def list_aws_products(profile=None, max_results=50, *, public_only=False, progress=None):
    """Enumerate all pages, then describe batches of at most 20; never return partial data."""
    client = aws_client(profile)
    summaries = {}
    token = None
    seen_tokens = set()
    while True:
        request: dict[str, Any] = {"EntityType": "AmiProduct", "MaxResults": max_results}
        if token:
            request["NextToken"] = token
        if public_only:
            request["EntityTypeFilters"] = {
                "AmiProductFilters": {"Visibility": {"ValueList": ["Public"]}},
            }
        response = aws_call(client, "list_entities", **request)
        rows = response.get("EntitySummaryList")
        if not isinstance(rows, list):
            raise InventoryError("AWS inventory has missing or malformed entity summaries")
        for summary in rows:
            if not isinstance(summary, dict) or not summary.get("EntityId"):
                raise InventoryError("AWS inventory contains a missing entity ID; inventory is incomplete")
            entity_id = summary["EntityId"]
            ami_summary = dict(summary.get("AmiProductSummary") or {})
            ami_summary.setdefault("Visibility", summary.get("Visibility") or "")
            if public_only and str(ami_summary["Visibility"]).casefold() != "public":
                continue
            if entity_id in summaries:
                raise InventoryError("AWS inventory repeated an entity ID; inventory is incomplete")
            summaries[entity_id] = ami_summary
        token = response.get("NextToken")
        if not token:
            break
        if token in seen_tokens:
            raise InventoryError("AWS pagination repeated a token; inventory is incomplete")
        seen_tokens.add(token)
    if progress:
        progress(f"AWS inventory listed: {len(summaries)} {'Public ' if public_only else ''}AMI products")
    products = []
    entity_ids = list(summaries)
    for offset in range(0, len(entity_ids), 20):
        batch = entity_ids[offset:offset + 20]
        response = aws_call(client, "batch_describe_entities", EntityRequestList=[
            {"Catalog": CATALOG, "EntityId": entity_id} for entity_id in batch
        ])
        details = response.get("EntityDetails") or {}
        if response.get("Errors") or any(entity_id not in details for entity_id in batch):
            raise InventoryError("AWS batch_describe_entities returned errors or missing details; inventory is incomplete")
        for entity_id in batch:
            product = _product(entity_id, details[entity_id], summaries[entity_id])
            if not public_only or product.visibility.casefold() == "public":
                products.append(product)
        if progress:
            progress(f"AWS inventory details: {offset + len(batch)}/{len(entity_ids)}")
    return products
