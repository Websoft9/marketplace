"""Mechanical validation for the local marketplace offer-codes dataset.

The offer-codes source (``data/offer-codes/`` by default) is the repository
source of truth for per-platform marketplace product codes. ``mk catalog pull``
and ``mk versions`` read it, so a malformed entry silently changes those
outputs. This module checks the structure that all readers assume and the
cross-entry rules (unique ids, one owner per code) that no reader enforces.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import marketplace

REQUIRED_PLATFORMS = ("aliyun", "huaweiyun", "alibabacloud", "huaweicloud", "azure", "aws")
REQUIRED_FIELDS = ("id", "productID", "type", "applications")
OPTIONAL_FIELDS = ("variants",)
KNOWN_TYPES = frozenset({"app", "websoft9"})
UNKNOWN_TYPE_SEVERITY = "warning"

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

# Observed vendor formats. A mismatch is a warning, not an error: legacy codes
# exist (AWS codes are 24 or 25 characters today) and must stay publishable.
CODE_PATTERNS: dict[str, re.Pattern[str]] = {
    "aliyun": re.compile(r"^cmjj[0-9]+$"),
    "alibabacloud": re.compile(r"^sgcmjj[0-9]+$"),
    "huaweiyun": re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    ),
    "azure": re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$"),
    "aws": re.compile(r"^[a-z0-9]{24,25}$"),
}

# Canonical length where the vendor format is fixed but legacy values exist.
CODE_LENGTHS: dict[str, int] = {"aws": 25}

def _issue(
    issues: list[dict[str, str]],
    severity: str,
    source: str,
    app: str | None,
    field: str | None,
    message: str,
    category: str | None = None,
) -> None:
    issues.append(
        {
            "severity": severity,
            "source": source,
            "app": app or "",
            "field": field or "",
            "message": message,
            "category": category or "",
        }
    )


def load_source(source: str, timeout: float = 30) -> list[tuple[str, Any]]:
    """Load one JSON document, or every ``*.json`` file in a directory.

    Returns ``(label, document)`` pairs so issues can point at the file that
    produced them. A directory is read in sorted filename order, matching
    ``marketplace.read_offers``.
    """
    resolved = marketplace.resolve_source(str(source))
    if isinstance(resolved, Path) and resolved.is_dir():
        files = sorted(resolved.glob("*.json"))
        if not files:
            raise ValueError(f"offer codes directory has no JSON files: {resolved}")
        return [(str(file), marketplace.load_json(str(file), timeout)) for file in files]
    return [(str(resolved), marketplace.load_json(str(resolved), timeout))]


def _entries(document: Any, source: str, issues: list[dict[str, str]]) -> list[Any]:
    entries = document.get("items") if isinstance(document, dict) else document
    if not isinstance(entries, list):
        _issue(
            issues,
            "error",
            source,
            None,
            None,
            "must be a JSON array or an object with an items array",
        )
        return []
    return entries


def verify(
    documents: list[tuple[str, Any]],
    *,
    catalog_ids: set[str] | None = None,
    check_coverage: bool = False,
) -> dict[str, Any]:
    """Validate offer-codes documents and return issues plus counts."""
    issues: list[dict[str, str]] = []

    total = 0
    seen_ids: dict[str, list[str]] = {}
    codes_by_platform: dict[str, dict[str, list[str]]] = {}

    for source, document in documents:
        for index, entry in enumerate(_entries(document, source, issues)):
            total += 1
            where = f"{source}[{index}]"
            if not isinstance(entry, dict):
                _issue(issues, "error", source, None, None, f"entry {index} is not an object")
                continue

            app = entry.get("id")
            app_id = app.strip() if isinstance(app, str) and app.strip() else None

            for field_name in REQUIRED_FIELDS:
                if field_name not in entry:
                    _issue(
                        issues, "error", source, app_id, field_name,
                        f"entry {index} is missing required field {field_name!r}",
                    )

            if not isinstance(app, str) or not app.strip():
                _issue(issues, "error", source, app_id, "id", "must be a non-empty string")
                app_id = None
            else:
                if app != app.strip():
                    _issue(issues, "error", source, app_id, "id", "has surrounding whitespace")
                if not ID_PATTERN.match(app.strip()):
                    _issue(
                        issues, "warning", source, app_id, "id",
                        "should use lowercase letters, digits, dot, dash or underscore",
                    )
                seen_ids.setdefault(app.strip(), []).append(source)

            entry_type = entry.get("type")
            if not isinstance(entry_type, str) or not entry_type.strip():
                _issue(issues, "error", source, app_id, "type", "must be a non-empty string")
            elif entry_type.strip() not in KNOWN_TYPES:
                _issue(
                    issues, UNKNOWN_TYPE_SEVERITY, source, app_id, "type",
                    f"unknown type {entry_type!r}; expected one of {sorted(KNOWN_TYPES)}",
                )

            applications = entry.get("applications")
            if not isinstance(applications, list):
                _issue(issues, "error", source, app_id, "applications", "must be an array")
            else:
                aliases: list[str] = []
                for alias in applications:
                    if not isinstance(alias, str) or not alias.strip():
                        _issue(
                            issues, "error", source, app_id, "applications",
                            f"contains a non-string or empty entry: {alias!r}",
                        )
                        continue
                    if alias != alias.strip():
                        _issue(
                            issues, "error", source, app_id, "applications",
                            f"{alias!r} has surrounding whitespace",
                        )
                    if not ID_PATTERN.match(alias.strip()):
                        _issue(
                            issues, "warning", source, app_id, "applications",
                            f"{alias!r} should use lowercase letters, digits, dot, dash or underscore",
                        )
                    if alias.strip() in aliases:
                        _issue(
                            issues, "error", source, app_id, "applications",
                            f"duplicates {alias.strip()!r}",
                        )
                    aliases.append(alias.strip())
                if entry_type == "app" and app_id and app_id not in aliases:
                    _issue(
                        issues, "warning", source, app_id, "applications",
                        "does not include the entry id",
                    )

            product_ids = entry.get("productID")
            if not isinstance(product_ids, dict):
                _issue(issues, "error", source, app_id, "productID", "must be an object")
                continue

            for platform in REQUIRED_PLATFORMS:
                if platform not in product_ids:
                    _issue(
                        issues, "error", source, app_id, f"productID.{platform}",
                        "missing platform key",
                    )
            for platform in product_ids:
                if platform not in REQUIRED_PLATFORMS:
                    _issue(
                        issues, "warning", source, app_id, f"productID.{platform}",
                        "platform key is not part of the standard set",
                    )

            for platform, codes in product_ids.items():
                field_name = f"productID.{platform}"
                if not isinstance(codes, list):
                    _issue(issues, "error", source, app_id, field_name, "must be an array")
                    continue
                seen_codes: set[str] = set()
                for code in codes:
                    if not isinstance(code, str) or not code.strip():
                        _issue(
                            issues, "error", source, app_id, field_name,
                            f"contains a non-string or empty code: {code!r}",
                        )
                        continue
                    if code != code.strip():
                        _issue(
                            issues, "error", source, app_id, field_name,
                            f"{code!r} has surrounding whitespace",
                        )
                    value = code.strip()
                    if value in seen_codes:
                        _issue(
                            issues, "error", source, app_id, field_name,
                            f"duplicates code {value!r}",
                        )
                    seen_codes.add(value)
                    pattern = CODE_PATTERNS.get(platform)
                    if pattern is not None and not pattern.match(value):
                        _issue(
                            issues, "warning", source, app_id, field_name,
                            f"code {value!r} does not match the expected {platform} format",
                        )
                    expected_length = CODE_LENGTHS.get(platform)
                    if expected_length and len(value) != expected_length:
                        _issue(
                            issues, "warning", source, app_id, field_name,
                            f"code {value!r} is {len(value)} characters; "
                            f"most {platform} codes use {expected_length}",
                        )
                    if app_id:
                        codes_by_platform.setdefault(platform, {}).setdefault(
                            value, []
                        ).append(app_id)
                    else:
                        codes_by_platform.setdefault(platform, {}).setdefault(value, [])

            extra = set(entry) - set(REQUIRED_FIELDS) - set(OPTIONAL_FIELDS)
            for field_name in sorted(extra):
                _issue(
                    issues, "warning", source, app_id, field_name,
                    "unknown top-level field",
                )

            _check_variants(
                entry.get("variants"), product_ids, app_id, source, issues
            )

    for app_id, sources in seen_ids.items():
        unique_sources = sorted(set(sources))
        if len(sources) > 1:
            if len(unique_sources) > 1:
                message = (
                    f"duplicate application id across {len(unique_sources)} file(s): "
                    + ", ".join(unique_sources)
                )
            else:
                message = "duplicate application id"
            _issue(issues, "error", unique_sources[0], app_id, "id", message)

    for platform, by_code in codes_by_platform.items():
        for code, owners in by_code.items():
            unique = sorted(set(owners))
            if len(unique) > 1:
                _issue(
                    issues, "warning", "", None, f"productID.{platform}",
                    f"code {code!r} is assigned to {len(unique)} applications: "
                    + ", ".join(unique),
                )

    if catalog_ids is not None:
        for app_id in sorted(seen_ids):
            if app_id not in catalog_ids:
                _issue(
                    issues, "warning", "", app_id, "id",
                    "application id is not present in product_en.json",
                    category="product",
                )
        if check_coverage:
            missing = sorted(catalog_ids - set(seen_ids))
            if missing:
                preview = ", ".join(missing[:10])
                suffix = "" if len(missing) <= 10 else f", and {len(missing) - 10} more"
                _issue(
                    issues, "warning", "", None, "applications",
                    f"{len(missing)} product_en.json application(s) have no offer entry: "
                    f"{preview}{suffix}",
                    category="product",
                )

    errors = sum(1 for issue in issues if issue["severity"] == "error")
    warnings = sum(1 for issue in issues if issue["severity"] == "warning")
    strict_warnings = sum(
        1
        for issue in issues
        if issue["severity"] == "warning" and issue.get("category") != "product"
    )
    return {
        "entries": total,
        "issues": issues,
        "errors": errors,
        "warnings": warnings,
        "strictWarnings": strict_warnings,
    }


def _check_variants(
    variants: Any,
    product_ids: dict[str, Any],
    app_id: str | None,
    source: str,
    issues: list[dict[str, str]],
) -> None:
    """Validate the optional platform -> code -> label mapping."""
    if variants is None:
        return
    if not isinstance(variants, dict):
        _issue(issues, "error", source, app_id, "variants", "must be an object")
        return
    for platform, labels in variants.items():
        field_name = f"variants.{platform}"
        codes = product_ids.get(platform)
        if platform not in product_ids or not isinstance(codes, list):
            _issue(
                issues, "error", source, app_id, field_name,
                f"platform has no productID entry for {platform!r}",
            )
            continue
        if not isinstance(labels, dict):
            _issue(issues, "error", source, app_id, field_name, "must be an object")
            continue
        if not labels and len(codes) > 1:
            _issue(
                issues, "error", source, app_id, field_name,
                "must label at least one code when productID has several codes",
            )
        for code, label in labels.items():
            code_field = f"{field_name}.{code}"
            if code not in codes:
                _issue(
                    issues, "error", source, app_id, code_field,
                    f"code {code!r} is not present in productID.{platform}",
                )
            if not isinstance(label, str):
                _issue(issues, "error", source, app_id, code_field, "must be a string")
                continue
            if label != label.strip():
                _issue(
                    issues, "error", source, app_id, code_field,
                    "has surrounding whitespace",
                )
            if any(not segment.strip() for segment in label.split(";")):
                _issue(
                    issues, "error", source, app_id, code_field,
                    "has an empty ';'-separated segment",
                )


def load_catalog_ids(source: str, timeout: float = 30) -> set[str]:
    """Return application ids from product_en.json or a catalog dataset."""
    document = marketplace.load_json(str(source), timeout)
    items = document.get("items") if isinstance(document, dict) else document
    if not isinstance(items, list):
        raise ValueError("catalog must be a JSON array or an object with an items array")
    ids: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        value = item.get("id", item.get("key"))
        if isinstance(value, str) and value.strip():
            ids.add(value.strip())
    return ids


def collect_codes(documents: list[tuple[str, Any]]) -> list[dict[str, Any]]:
    """Return every platform/code pair once, with the applications that use it."""
    codes: dict[tuple[str, str], set[str]] = {}
    for _source, document in documents:
        entries = document.get("items") if isinstance(document, dict) else document
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            app = entry.get("id")
            app_id = app.strip() if isinstance(app, str) and app.strip() else None
            product_ids = entry.get("productID")
            if not isinstance(product_ids, dict):
                continue
            for platform, values in product_ids.items():
                if not isinstance(values, list):
                    continue
                for code in values:
                    if isinstance(code, str) and code.strip():
                        owners = codes.setdefault((str(platform), code.strip()), set())
                        if app_id:
                            owners.add(app_id)
    return [
        {"platform": platform, "code": code, "apps": sorted(owners)}
        for (platform, code), owners in codes.items()
    ]
