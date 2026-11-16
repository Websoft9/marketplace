"""Generate the compatible application/code matrix from live cloud inventory."""

from __future__ import annotations

import copy
import json
import os
import tempfile
from collections import Counter
from pathlib import Path

from . import offer_codes, offer_resolve


def generate(products, identities, previous, *, platform="aws", codes=None):
    """Full runs replace one cloud; targeted runs replace only selected codes.

    No old matrix is used to infer ownership. Uncertain listings are excluded
    and reported without blocking independently resolved listings.
    """
    if platform != "aws":
        raise ValueError(f"generation is not supported for {platform!r}; implemented: aws")
    products = list(products)
    selected = list(products)
    unresolved = []
    if codes:
        selected = []
        for code in codes:
            matches = [p for p in products if code in {p.code, p.alternate_code, p.entity_id}]
            if len(matches) != 1:
                unresolved.append({
                    "code": code, "title": "", "entityId": "", "candidates": [],
                    "reason": "selected code not found" if not matches else
                              "selected code matches multiple AWS products",
                })
                # Include every match so duplicate aliases cannot hide conflicts.
                for product in matches:
                    if product not in selected:
                        selected.append(product)
                continue
            if matches[0] not in selected:
                selected.append(matches[0])
    if not products:
        raise ValueError("empty AWS inventory; refusing to clear existing mappings")

    assignments = {}
    code_counts = Counter(product.code for product in products if product.code)
    ambiguous_aliases = {
        code for code in codes or []
        if sum(code in {p.code, p.alternate_code, p.entity_id} for p in products) > 1
    }
    for product in selected:
        owners = offer_resolve.identify(product.title, product.summary, identities)
        reason = (
            "missing product code (SKU aliases are not codes)" if not product.code else
            "duplicate cloud code" if code_counts[product.code] > 1 else
            "selected code matches multiple AWS products" if ambiguous_aliases &
                {product.code, product.alternate_code, product.entity_id} else
            "ambiguous application" if len(owners) > 1 else
            "no application match" if not owners else ""
        )
        if reason:
            unresolved.append({
                "code": product.code, "title": product.title,
                "entityId": product.entity_id,
                "candidates": sorted(owners),
                "reason": reason,
            })
            continue
        assignments[product.code] = next(iter(owners))
    report = {
        "platform": platform, "mode": "codes" if codes else "platform",
        "checked": len(selected), "matched": len(assignments), "unresolved": unresolved,
    }
    if not assignments:
        return None, report

    by_id = {entry["id"]: copy.deepcopy(entry) for entry in previous}
    selected_aliases = {
        value for product in selected
        if product.code in assignments
        for value in (product.code, product.alternate_code, product.entity_id) if value
    }
    for entry in by_id.values():
        values = entry["productID"][platform]
        touched = selected_aliases if codes else set(values)
        entry["productID"][platform] = [code for code in values if code not in touched]
        # Variants are no longer maintained. Keep no stale per-code labels.
        entry.pop("variants", None)
    for code, app in sorted(assignments.items()):
        entry = by_id.setdefault(app, {
            "id": app, "productID": {key: [] for key in offer_codes.REQUIRED_PLATFORMS},
            "type": "app", "applications": [app],
        })
        entry["productID"][platform].append(code)
    result = sorted(by_id.values(), key=lambda entry: entry["id"])
    for entry in result:
        for key in entry["productID"]:
            entry["productID"][key] = sorted(set(entry["productID"][key]))
    validation = offer_codes.verify([("generated matrix", result)], catalog_ids=set(identities))
    if validation["errors"]:
        raise ValueError("generated matrix failed verification: " + "; ".join(
            issue["message"] for issue in validation["issues"] if issue["severity"] == "error"
        ))
    return result, report


def write_matrix(path: Path, matrix: list, expected: bytes | None):
    """Use the offer-source directory lock; backups never match the *.json glob."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with offer_codes.source_lock(str(path)):
        current = path.read_bytes() if path.exists() else None
        if current != expected:
            raise ValueError("matrix changed during generation; refusing to overwrite")
        pending = None
        try:
            if expected is not None:
                with tempfile.NamedTemporaryFile(
                    dir=path.parent, prefix=f".{path.name}.before-", suffix=".bak", delete=False
                ) as backup:
                    backup.write(expected)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=f".{path.name}.", suffix=".tmp", delete=False,
            ) as output:
                pending = Path(output.name)
                output.write(offer_codes.serialize_document(matrix))
                output.flush()
                os.fsync(output.fileno())
            os.replace(pending, path)
        finally:
            if pending is not None:
                pending.unlink(missing_ok=True)
