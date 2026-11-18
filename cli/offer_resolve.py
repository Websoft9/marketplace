"""Resolve live AWS Marketplace listings to offer targets by application.

The resolver reads the seller Catalog instead of the local offer-code matrix.
It matches an application by trademark in the listing title or short
description, detects the offer variant (OS and non-OS labels) and requires one
code per variant.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# Compatibility exports for existing offer-command callers; collection is catalog-independent.
from .aws_inventory import _product, list_aws_products
from .store_models import Product
from .os_catalog import OS_NAMES, OS_DISPLAY
from .marketplace import load_json

BASE = "base"
IDENTITY_RULES_VERSION = "deterministic-first-appearance-v2"


class IdentityCatalogError(ValueError):
    """Catalog validation failure with an explicitly safe diagnostic message."""


class IdentityCatalog(dict[str, list[str]]):
    """Effective identity names plus record-level degradation diagnostics."""

    def __init__(self):
        super().__init__()
        self.warnings: list[dict[str, str]] = []


def normalize(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


def contains(text: str, term: str) -> bool:
    """Token containment for OS/variant labels; identity matching uses the v2 engine."""
    text = normalize(text)
    term = normalize(term)
    if not term:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])"
    return re.search(pattern, text) is not None


def summary_lead(summary: str) -> str:
    """Only the text before the first English/Chinese comma is identity evidence."""
    return re.split(r"[,，]", summary, maxsplit=1)[0]


def load_identities(source: str, special_cases: dict | None = None) -> IdentityCatalog:
    """Degrade missing names to reviewed branding or safe IDs, never drop records."""
    try:
        data = load_json(source)
    except (OSError, ValueError) as error:
        raise IdentityCatalogError(f"cannot read application identity catalog ({type(error).__name__})") from None
    if not isinstance(data, list):
        raise IdentityCatalogError("product catalog must be an array")
    result = IdentityCatalog()
    for index, row in enumerate(data):
        if not isinstance(row, dict):
            raise IdentityCatalogError(f"invalid product catalog item at record {index}")
        app, trademark = row.get("key"), row.get("trademark")
        if not isinstance(app, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", app):
            raise IdentityCatalogError(f"product catalog requires valid lowercase application keys (record {index})")
        if app in result:
            raise IdentityCatalogError(f"duplicate application key {app!r} in product catalog (record {index})")
        if trademark is not None and not isinstance(trademark, str):
            raise IdentityCatalogError(f"product catalog {app!r} has an invalid trademark type")
        usable_trademark = isinstance(trademark, str) and bool(normalize_identity(trademark))
        names = [trademark] if usable_trademark else []
        branding = ((special_cases or {}).get(app) or {}).get("titleTrademark")
        if branding is not None and (not isinstance(branding, str) or not normalize_identity(branding)):
            raise IdentityCatalogError(f"invalid reviewed titleTrademark for application {app!r}")
        if branding and branding not in names:
            names.append(branding)
        if not usable_trademark:
            if not names and not _safe_id_name(normalize_identity(app)):
                raise IdentityCatalogError(
                    f"product catalog {app!r} has no usable trademark or reviewed name, "
                    "and its ID is unsafe for automatic fallback; nothing written"
                )
            result.warnings.append({
                "code": "missing-trademark", "appId": app,
                "fallback": "title-trademark" if names else "app-id",
                "message": ("Missing/empty trademark; using reviewed titleTrademark."
                            if names else "Missing/empty trademark; using safe application ID fallback."),
            })
        result[app] = names
    if not result:
        raise IdentityCatalogError("product catalog is empty")
    return result


def catalog_warnings(identities):
    """Also accept plain dictionaries used by existing callers and test fixtures."""
    return list(getattr(identities, "warnings", []))


def identify(title: str, summary: str, identities: dict[str, list[str]]) -> set[str]:
    """Title wins; select first appearance, retaining only true positional ties."""
    return identify_evidence(title, summary, identities)[0]


def identify_evidence(title: str, summary: str, identities: dict[str, list[str]]):
    """Compatibility interface for the shared, explainable identity selector."""
    result = identify_details(title, summary, identities)
    owners = {result["appId"]} if result["appId"] else set(result["candidates"])
    return owners, result["method"]


def normalize_identity(value: str) -> str:
    # Remove marks before NFKC, otherwise ™ becomes the literal letters "TM".
    value = str(value or "").translate(str.maketrans("", "", "™®℠"))
    value = unicodedata.normalize("NFKC", value).casefold()
    value = value.translate(str.maketrans({c: "-" for c in "‐‑‒–—−"}))
    return " ".join(value.split())


def _safe_id_name(value):
    """Do not turn short generic IDs (go, r, ai, php) into new aliases."""
    return (re.fullmatch(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*", value) is not None
            and (len(value) >= 4 or any(c.isdigit() for c in value))
            # Reuse the OS vocabulary, not a separately maintained app denylist:
            # "Oracle" alone is not evidence for the Oracle Database application.
            and not any(name.startswith(value + " ") for name in OS_NAMES))


def _identity_hit(text, app, names, include_ids):
    full = sorted({normalize_identity(name) for name in names if normalize_identity(name)})
    shortened = sorted({name[7:].strip() for name in full
                        if name.startswith("apache ") and len(name[7:].strip()) >= 4})
    app_name = normalize_identity(app)
    tiers = [("trademark", full), ("apache-short-name", shortened)]
    if include_ids and _safe_id_name(app_name):
        tiers.append(("app-id", [app_name]))
    # Prefer the complete trademark for THIS application. Only if absent, use
    # a derived form; a different application's late full name cannot suppress
    # an earlier short name ("Superset with PHP" must not become PHP).
    for rank, (kind, terms) in enumerate(tiers):
        hits = []
        for term in terms:
            found = re.search(r"(?<![a-z0-9_])" + re.escape(term) + r"(?![a-z0-9_])", text)
            if found:
                hits.append({"appId": app, "name": term, "start": found.start(),
                             "end": found.end(), "kind": kind, "rank": rank})
        if hits:
            return min(hits, key=lambda h: (h["start"], -(h["end"] - h["start"]), h["name"]))
    return None


def identify_details(title, summary, identities, *, include_ids=True):
    """Title first; per-app full-name fallback; earliest occurrence across apps.

    At the same start position prefer stronger evidence, then the longer name.
    Truly tied application identities remain ambiguous, never catalog-order based.
    Positions refer to normalized text, not offsets in the original cloud title.
    """
    for field, raw in (("title", title), ("summary-lead", summary_lead(summary))):
        text = normalize_identity(raw)
        hits = [hit for app, names in identities.items()
                if (hit := _identity_hit(text, app, names, include_ids)) is not None]
        if not hits:
            continue
        priority = lambda h: (h["start"], h["rank"], -(h["end"] - h["start"]))
        best = min(map(priority, hits))
        winners = {h["appId"] for h in hits if priority(h) == best}
        hits.sort(key=lambda h: (*priority(h), h["appId"]))
        evidence = [{k: v for k, v in h.items() if k != "rank"} | {
            "field": field, "selected": h["appId"] in winners,
        } for h in hits]
        return {
            "status": "matched" if len(winners) == 1 else "ambiguous",
            "appId": next(iter(winners)) if len(winners) == 1 else None,
            "candidates": sorted(winners) if len(winners) > 1 else [],
            "method": field + "-" + hits[0]["kind"], "evidence": evidence,
            "selection": "first-appearance",
        }
    return {"status": "unmatched", "appId": None, "candidates": [], "method": None,
            "evidence": [], "selection": "first-appearance"}


def match_trademark(title: str, summary: str, trademark: str) -> bool:
    return identify_details(title, summary, {"target": [trademark]}, include_ids=False)["appId"] is not None


def detect_os(text: str) -> list[str]:
    """Return distinct OS display names found in text, longest match first."""
    normalized = normalize(text)
    found: list[str] = []
    for name in sorted(OS_NAMES, key=len, reverse=True):
        if contains(normalized, name):
            display = OS_DISPLAY[name]
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


def _target(product: Product, offer: str, match: str, identities=None) -> dict[str, Any]:
    result = {
        "code": product.code or product.entity_id,
        "entityId": product.entity_id,
        "offer": offer or "",
        "title": product.title,
        "visibility": product.visibility,
        "match": match,
    }
    if identities is not None:
        result["appMatch"] = identify_details(product.title, product.summary, identities)
        result["identityRulesVersion"] = IDENTITY_RULES_VERSION
    return result


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
    if not any(normalize_identity(name) for name in identities[app]) and not _safe_id_name(normalize_identity(app)):
        raise ValueError(f"application {app!r} has no usable identity; restore its trademark before resolving targets")

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
        targets = [_target(product, variant, "app" if matched else "override", identities)]
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
        targets = [_target(selected[0], requested, "app", identities)]
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
            targets.append(_target(group[0], group[0].offer, "app", identities))

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
