"""Resolve live AWS Marketplace listings to offer targets by application.

The resolver reads the seller Catalog instead of the local offer-code matrix.
It matches an application by trademark in the listing title or short
description, detects the offer variant (OS and non-OS labels) and requires one
code per variant.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .aws_marketplace import CATALOG, aws_call, aws_client
from .offer import OS_NAMES
from .marketplace import load_json

OS_DISPLAY = {
    "amazon linux": "Amazon Linux",
    "oracle linux": "Oracle Linux",
    "red hat": "Red Hat",
    "rocky linux": "Rocky Linux",
    "almalinux": "AlmaLinux",
    "alma linux": "AlmaLinux",
    "centos": "CentOS",
    "debian": "Debian",
    "ubuntu": "Ubuntu",
    "suse": "SUSE",
    "sles": "SLES",
    "fedora": "Fedora",
    "windows server": "Windows Server",
    "freebsd": "FreeBSD",
    "rhel": "RHEL",
}

BASE = "base"


@dataclass
class Product:
    code: str
    entity_id: str
    title: str
    summary: str = ""
    visibility: str = ""
    offer: str = ""
    alternate_code: str = ""


def normalize(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


def contains(text: str, term: str) -> bool:
    text = normalize(text)
    term = normalize(term)
    if not term:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])"
    return re.search(pattern, text) is not None


def summary_lead(summary: str) -> str:
    """Only the text before the first English/Chinese comma is identity evidence."""
    return re.split(r"[,，]", summary, maxsplit=1)[0]


def load_identities(source: str, special_cases: dict | None = None) -> dict[str, list[str]]:
    """Load the full store catalog, including reviewed title-brand exceptions."""
    data = load_json(source)
    if not isinstance(data, list):
        raise ValueError("product catalog must be an array")
    result = {}
    for row in data:
        if not isinstance(row, dict):
            raise ValueError("invalid product catalog item")
        app, trademark = row.get("key"), row.get("trademark")
        if not isinstance(app, str) or not app.strip() or app in result:
            raise ValueError("product catalog requires unique non-empty application keys")
        if not isinstance(trademark, str) or not trademark.strip():
            raise ValueError(f"product catalog {app!r} has no trademark")
        names = [trademark]
        branding = ((special_cases or {}).get(app) or {}).get("titleTrademark")
        if branding and branding not in names:
            names.append(branding)
        result[app] = names
    if not result:
        raise ValueError("product catalog is empty")
    return result


def identify(title: str, summary: str, identities: dict[str, list[str]]) -> set[str]:
    """Title wins, even when it identifies a different or several applications."""
    for text in (title, summary_lead(summary)):
        matches = {
            app for app, names in identities.items()
            if any(contains(text, name) for name in names)
        }
        if matches:
            return matches
    return set()


def match_trademark(title: str, summary: str, trademark: str) -> bool:
    return bool(identify(title, summary, {"target": [trademark]}))


def detect_os(text: str) -> list[str]:
    """Return distinct OS display names found in text, longest match first."""
    normalized = normalize(text)
    found: list[str] = []
    for name in sorted(OS_NAMES, key=len, reverse=True):
        if contains(normalized, name):
            display = OS_DISPLAY.get(name, name.title())
            if display not in found:
                found.append(display)
    return found


def offer_parts(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").split(";") if part.strip()]


def variant_key(value: str) -> frozenset[str]:
    return frozenset(part.casefold() for part in offer_parts(value))


def declared_labels(multi_offers: list[str] | None) -> list[str]:
    labels: dict[str, str] = {}
    for entry in multi_offers or []:
        for part in offer_parts(entry):
            if not is_os_part(part):
                labels.setdefault(part.casefold(), part)
    return list(labels.values())


def is_os_part(part: str) -> bool:
    return normalize(part) in {name for name in OS_NAMES}


def declared_offers(multi_offers: list[str] | None) -> set[frozenset[str]]:
    return {variant_key(entry) for entry in multi_offers or []}


def detect_offer(title: str, summary: str, multi_offers: list[str] | None) -> str:
    """Compose the offer variant detected in a listing title/summary.

    The title is authoritative; the summary is a fallback for listings whose
    title does not state the OS or label.
    """
    os_values = detect_os(title) or detect_os(summary_lead(summary))
    if len(os_values) > 1:
        raise ValueError(
            "multiple operating systems detected: " + ", ".join(os_values)
        )
    labels = [
        label
        for label in declared_labels(multi_offers)
        if contains(title, label)
    ]
    # A summary can advertise support for Multisite without being that offer.
    # Non-OS labels therefore require title evidence, never capability copy.
    parts = ([os_values[0]] if os_values else []) + labels
    return "; ".join(parts)


def normalize_offer(value: str | None) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    if not value or value.casefold() == BASE:
        return ""
    return value


def canonical_offer(value: str, multi_offers: list[str] | None) -> str:
    """Return the declared spelling for one variant, preserving display case."""
    key = variant_key(value)
    for entry in multi_offers or []:
        if variant_key(entry) == key:
            return entry
    return value


def validate_offer_choice(bundle: dict, offer: str | None) -> str | None:
    requested = normalize_offer(offer)
    if requested is None:
        return None
    multi_offers = bundle.get("multiOffers")
    app = bundle.get("key")
    if multi_offers is None or variant_key(requested) not in declared_offers(multi_offers):
        raise ValueError(f"offer {requested or BASE!r} is not declared in multiOffers for {app!r}")
    return canonical_offer(requested, multi_offers)


def _product(entity_id: str, entity: dict, summary: dict) -> Product:
    detail = entity.get("DetailsDocument")
    if detail is None:
        detail = json.loads(entity.get("Details") or "{}")
    description = detail.get("Description") or {}
    title = str(description.get("ProductTitle") or summary.get("ProductTitle") or "")
    short = str(description.get("ShortDescription") or "")
    sku = str(description.get("Sku") or "")
    product_code = str(description.get("ProductCode") or "")
    # Sku is a seller-defined alias, never a public marketplace product code.
    code = product_code
    alternate = sku if sku and sku != code else ""
    visibility = str(description.get("Visibility") or summary.get("Visibility") or "")
    return Product(
        code=code,
        entity_id=entity_id,
        title=title,
        summary=short,
        visibility=visibility,
        alternate_code=alternate,
    )


def list_aws_products(
    profile: str | None = None, max_results: int = 50, *, public_only: bool = False,
    progress=None,
) -> list[Product]:
    """Enumerate all pages first, then describe products in batches of at most 20.

    Public-only inventory excludes Restricted, Limited, Draft and unknown states.
    Batch failures and missing details never return an apparently complete list.
    """
    client = aws_client(profile)
    summaries = {}
    token = None
    seen_tokens: set[str] = set()
    while True:
        request: dict[str, Any] = {
            "EntityType": "AmiProduct",
            "MaxResults": max_results,
        }
        if token:
            request["NextToken"] = token
        if public_only:
            request["EntityTypeFilters"] = {
                "AmiProductFilters": {"Visibility": {"ValueList": ["Public"]}},
            }
        response = aws_call(client, "list_entities", **request)
        for summary in response.get("EntitySummaryList", []):
            entity_id = summary.get("EntityId")
            if not entity_id:
                raise ValueError("AWS inventory contains a missing entity ID; inventory is incomplete")
            ami_summary = dict(summary.get("AmiProductSummary") or {})
            ami_summary.setdefault("Visibility", summary.get("Visibility") or "")
            if public_only and normalize(ami_summary["Visibility"]) != "public":
                continue
            if entity_id in summaries:
                raise ValueError("AWS inventory repeated an entity ID; inventory is incomplete")
            summaries[entity_id] = ami_summary
        token = response.get("NextToken")
        if not token:
            break
        if token in seen_tokens:
            raise ValueError("AWS pagination repeated a token; inventory is incomplete")
        seen_tokens.add(token)
    if progress:
        progress(f"AWS inventory listed: {len(summaries)} {'Public ' if public_only else ''}AMI products")
    products: list[Product] = []
    entity_ids = list(summaries)
    for offset in range(0, len(entity_ids), 20):
        batch = entity_ids[offset:offset + 20]
        response = aws_call(client, "batch_describe_entities", EntityRequestList=[
            {"Catalog": CATALOG, "EntityId": entity_id} for entity_id in batch
        ])
        details = response.get("EntityDetails") or {}
        if response.get("Errors") or any(entity_id not in details for entity_id in batch):
            raise ValueError("AWS batch_describe_entities returned errors or missing details; inventory is incomplete")
        for entity_id in batch:
            product = _product(entity_id, details[entity_id], summaries[entity_id])
            # Visibility can change between listing and reading the details.
            if not public_only or normalize(product.visibility) == "public":
                products.append(product)
        if progress:
            progress(f"AWS inventory details: {offset + len(batch)}/{len(entity_ids)}")
    return products


def _target(product: Product, offer: str, match: str) -> dict[str, Any]:
    return {
        "code": product.code or product.entity_id,
        "entityId": product.entity_id,
        "offer": offer or "",
        "title": product.title,
        "visibility": product.visibility,
        "match": match,
    }


def _ordered(targets: list[dict[str, Any]], multi_offers: list[str] | None) -> list[dict[str, Any]]:
    order = {variant_key(entry): index for index, entry in enumerate(multi_offers or [])}

    def key(target: dict[str, Any]) -> tuple[int, str]:
        return order.get(variant_key(target.get("offer") or ""), len(order)), str(
            target.get("code") or target.get("entityId") or ""
        )

    return sorted(targets, key=key)


def resolve(
    bundle: dict[str, Any],
    products: list[Product],
    *,
    code: str | None = None,
    offer: str | None = None,
    identities: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Select targets from live products for one application.

    ``code`` targets one product and treats a different trademark as a forced
    override. Without ``code``, the application is matched by trademark and each
    declared or detected offer variant must map to exactly one code.
    """
    app = str(bundle.get("key") or "")
    if not app or re.search(r"[,，\s]", app):
        raise ValueError("exactly one application id is required; lists are not supported")
    trademark = str(bundle.get("trademark") or "").strip()
    multi_offers = bundle.get("multiOffers")
    declared = declared_offers(multi_offers)
    requested = validate_offer_choice(bundle, offer)
    identities = identities or {app: [trademark]}
    if app not in identities:
        raise ValueError(f"application {app!r} is absent from the product catalog")

    if code:
        matches = [
                item
                for item in products
                if code in {item.code, item.alternate_code, item.entity_id}
        ]
        if not matches:
            raise ValueError(f"AWS offer code {code!r} was not found")
        if len(matches) != 1:
            raise ValueError(f"AWS code {code!r} matches several entities; cannot distinguish")
        product = matches[0]
        matched = identify(product.title, product.summary, identities) == {app}
        variant = requested if requested is not None else detect_offer(
            product.title, product.summary, multi_offers
        )
        targets = [_target(product, variant, "app" if matched else "override")]
        return {
            "schemaVersion": 1,
            "app": app,
            "cloud": "aws",
            "code": code,
            "offer": variant,
            "targets": targets,
            "unresolved": [],
        }

    candidates = []
    for item in products:
        owners = identify(item.title, item.summary, identities)
        if app in owners:
            if len(owners) != 1:
                raise ValueError(
                    f"ambiguous application identity for {item.code!r}: "
                    + ", ".join(sorted(owners)) + "; specify --code for an override"
                )
            candidates.append(item)
    if not candidates:
        raise ValueError(
            f"no AWS product matches application {app!r} by trademark {trademark!r}"
        )
    for product in candidates:
        product.offer = detect_offer(product.title, product.summary, multi_offers)

    if requested is not None:
        wanted = variant_key(requested)
        selected = [item for item in candidates if variant_key(item.offer) == wanted]
        if not selected:
            raise ValueError(
                f"no AWS product for {app!r} matches offer {requested or BASE!r}"
            )
        if len(selected) > 1:
            raise ValueError(
                f"offer {requested or BASE!r} matches {len(selected)} AWS products; "
                "cannot distinguish"
            )
        targets = [_target(selected[0], requested, "app")]
    else:
        groups: dict[frozenset[str], list[Product]] = {}
        for product in candidates:
            groups.setdefault(variant_key(product.offer), []).append(product)
        targets = []
        for key, group in groups.items():
            if len(group) > 1:
                label = group[0].offer or BASE
                raise ValueError(
                    f"offer {label!r} matches {len(group)} AWS products; "
                    "cannot distinguish"
                )
            if multi_offers is not None and key not in declared:
                raise ValueError(
                    f"detected offer {group[0].offer or BASE!r} is not declared "
                    f"in multiOffers for {app!r}"
                )
            targets.append(_target(group[0], group[0].offer, "app"))

    found = {variant_key(target["offer"]) for target in targets}
    unresolved = (
        [
            {
                "offer": entry,
                "reason": "no live product matches this declared offer",
            }
            for entry in (multi_offers or [])
            if variant_key(entry) not in found
        ]
        if requested is None
        else []
    )
    return {
        "schemaVersion": 1,
        "app": app,
        "cloud": "aws",
        "code": None,
        "offer": requested,
        "targets": _ordered(targets, multi_offers),
        "unresolved": unresolved,
    }
