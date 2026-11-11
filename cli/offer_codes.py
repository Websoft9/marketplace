"""Mechanical validation for the local marketplace offer-codes dataset.

The offer-codes source (``data/offer-codes/`` by default) is the repository
source of truth for per-platform marketplace product codes. ``mk catalog pull``
and ``mk versions`` read it, so a malformed entry silently changes those
outputs. This module checks the structure that all readers assume and the
cross-entry rules (unique ids, one owner per code) that no reader enforces.
"""

from __future__ import annotations

import copy
import fcntl
import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager
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


def _document_items(document: Any) -> list[Any] | None:
    items = document.get("items") if isinstance(document, dict) else document
    return items if isinstance(items, list) else None


def require_structure(documents: list[tuple[str, Any]]) -> None:
    """Reject broken roots and malformed entries before a read-only audit."""
    result = verify(documents)
    errors = [issue for issue in result["issues"] if issue["severity"] == "error"]
    if errors:
        details = "; ".join(
            f"{item['source']} {item['app']} {item['field']}: {item['message']}"
            for item in errors
        )
        raise ValueError(f"invalid offer source: {details}")


@contextmanager
def source_lock(source: str):
    """Serialize CLI writers for a directory, including single-file overrides."""
    resolved = marketplace.resolve_source(source)
    if not isinstance(resolved, Path):
        raise ValueError("editing requires a local offer source")
    path = resolved.resolve()
    directory = path if path.is_dir() else path.parent
    with (directory / ".offer-codes.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _entries(document: Any, source: str, issues: list[dict[str, str]]) -> list[Any]:
    entries = _document_items(document)
    if entries is None:
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
    alias_owners: dict[str, list[int]] = {}
    owner_names: dict[int, str] = {}
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
            if isinstance(entry_type, str) and entry_type != entry_type.strip():
                _issue(issues, "error", source, app_id, "type", "has surrounding whitespace")

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

            if app_id:
                owner_names[total] = app_id
                for alias in {app_id, *_normalize(applications)}:
                    alias_owners.setdefault(alias, []).append(total)

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

    for alias, owners in alias_owners.items():
        if len(owners) > 1:
            _issue(
                issues, "error", "", None, "applications",
                f"ambiguous application id or alias {alias!r} matches entries: "
                + ", ".join(owner_names[owner] for owner in owners),
            )

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


def _entry_id(entry: Any) -> str | None:
    value = entry.get("id") if isinstance(entry, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _owner_ids(entry: Any) -> set[str]:
    """Return the entry id plus its ``applications`` aliases."""
    app_id = _entry_id(entry)
    owners = set(_normalize(entry.get("applications"))) if isinstance(entry, dict) else set()
    if app_id:
        owners.add(app_id)
    return owners


def _find_entries(
    documents: list[tuple[str, Any]], app_id: str
) -> list[tuple[str, Any, int, dict[str, Any]]]:
    matches: list[tuple[str, Any, int, dict[str, Any]]] = []
    for source, document in documents:
        for index, entry in enumerate(_document_items(document) or []):
            if app_id in _owner_ids(entry):
                matches.append((source, document, index, entry))
    return matches


def _take_label(entry: dict[str, Any], platform: str, code: str) -> str | None:
    variants = entry.get("variants")
    if not isinstance(variants, dict):
        return None
    labels = variants.get(platform)
    if not isinstance(labels, dict) or code not in labels:
        return None
    label = labels.pop(code)
    if not labels:
        variants.pop(platform, None)
        if not variants:
            entry.pop("variants", None)
    if not isinstance(label, str):
        raise ValueError(
            f"variant label for code {code!r} on {platform} is not a string"
        )
    return label


def _put_label(entry: dict[str, Any], platform: str, code: str, label: str) -> None:
    variants = entry.setdefault("variants", {})
    variants.setdefault(platform, {})[code] = label


def _commit_documents(
    documents: list[tuple[str, Any]], working: list[tuple[str, Any]]
) -> None:
    """Copy a fully validated working copy back into the original documents."""
    if len(documents) != len(working):
        raise RuntimeError("offer document count changed during edit")
    for (_, original), (_, updated) in zip(documents, working):
        if not ((isinstance(original, dict) and isinstance(updated, dict))
                or (isinstance(original, list) and isinstance(updated, list))):
            raise ValueError("offer document is not an array or an item object")
    for (_, original), (_, updated) in zip(documents, working):
        if isinstance(original, dict) and isinstance(updated, dict):
            original.clear()
            original.update(updated)
        elif isinstance(original, list) and isinstance(updated, list):
            original[:] = updated


def replace_code(
    documents: list[tuple[str, Any]],
    *,
    old_app: str,
    new_app: str,
    platforms: list[str] | None = None,
    codes: list[str] | None = None,
    delete_empty: bool = False,
) -> dict[str, Any]:
    """Move offer codes and their labels from one application entry to another.

    The original documents are updated only when the whole move succeeds; a
    validation error leaves them unchanged.
    """
    working = copy.deepcopy(documents)
    plan = _replace_code(
        working,
        old_app=old_app,
        new_app=new_app,
        platforms=platforms,
        codes=codes,
        delete_empty=delete_empty,
    )
    _commit_documents(documents, working)
    return plan


def _replace_code(
    documents: list[tuple[str, Any]],
    *,
    old_app: str,
    new_app: str,
    platforms: list[str] | None = None,
    codes: list[str] | None = None,
    delete_empty: bool = False,
) -> dict[str, Any]:
    """Move codes in a working copy; see :func:`replace_code`."""
    old_app = (old_app or "").strip()
    new_app = (new_app or "").strip()
    if not old_app or not new_app:
        raise ValueError("--from and --to must be non-empty application ids")
    if old_app == new_app:
        raise ValueError("--from and --to must be different applications")

    old_source, old_document, old_index, old_entry = _find_entry(
        documents, old_app, allow_missing=False
    )
    old_id = _entry_id(old_entry) or old_app
    target_match = _find_entry(documents, new_app, allow_missing=True)
    if target_match is not None and target_match[3] is old_entry:
        raise ValueError(
            f"{old_app!r} and {new_app!r} resolve to the same offer entry "
            f"{old_id!r}"
        )

    old_product_ids = old_entry.get("productID")
    if not isinstance(old_product_ids, dict):
        raise ValueError(f"entry {old_app!r} has no productID object")

    requested_codes = [
        str(code).strip() for code in (codes or []) if str(code).strip()
    ]
    requested_codes = list(dict.fromkeys(requested_codes))
    if codes is not None and not requested_codes:
        raise ValueError("explicit code selection must not be empty")
    if platforms is not None and not platforms:
        raise ValueError("explicit platform selection must not be empty")

    if platforms:
        selected = list(
            dict.fromkeys(
                str(item).strip() for item in platforms if str(item).strip()
            )
        )
        if not selected:
            raise ValueError("--platform must contain at least one platform")
        unknown = [
            platform for platform in selected if platform not in old_product_ids
        ]
        if unknown:
            raise ValueError(
                f"application {old_app!r} has no productID entry for "
                + ", ".join(unknown)
            )
    else:
        selected = [
            platform
            for platform, values in old_product_ids.items()
            if isinstance(values, list) and values
        ]

    if not selected:
        raise ValueError(f"application {old_app!r} has no offer codes to replace")

    owned: dict[str, list[str]] = {}
    for platform in selected:
        values = old_product_ids.get(platform)
        owned[platform] = (
            [value for value in values if isinstance(value, str) and value.strip()]
            if isinstance(values, list)
            else []
        )

    if requested_codes:
        for code in requested_codes:
            if not any(code in values for values in owned.values()):
                elsewhere = [
                    platform
                    for platform, values in old_product_ids.items()
                    if platform not in selected
                    and isinstance(values, list)
                    and code in values
                ]
                if elsewhere:
                    raise ValueError(
                        f"code {code!r} belongs to {old_app!r} on "
                        f"{', '.join(elsewhere)}, which is outside the selected platform(s)"
                    )
                known = sorted(
                    {value for values in owned.values() for value in values}
                )
                raise ValueError(
                    f"code {code!r} does not belong to {old_app!r} on the selected "
                    f"platform(s) (known: {', '.join(known) or 'none'})"
                )
        chosen = {
            platform: [code for code in values if code in requested_codes]
            for platform, values in owned.items()
        }
    else:
        chosen = owned

    if not any(chosen.values()):
        raise ValueError(f"application {old_app!r} has no offer codes to replace")

    created = target_match is None
    if created:
        target_entry: dict[str, Any] = {
            "id": new_app,
            "productID": {platform: [] for platform in REQUIRED_PLATFORMS},
            "type": "app",
            "applications": [new_app],
        }
        target_source = old_source
    else:
        (
            target_source,
            _target_document,
            _target_index,
            target_entry,
        ) = target_match
        if not isinstance(target_entry.get("productID"), dict):
            raise ValueError(f"entry {new_app!r} has no productID object")
        target_entry.setdefault("productID", {})
    new_id = _entry_id(target_entry) or new_app

    target_product_ids = target_entry["productID"]
    moves: list[dict[str, Any]] = []
    for platform, selected_codes in chosen.items():
        if not selected_codes:
            continue
        values = old_product_ids[platform]
        target_values = target_product_ids.setdefault(platform, [])
        if not isinstance(target_values, list):
            raise ValueError(
                f"productID.{platform} of {new_app!r} is not an array"
            )
        for code in selected_codes:
            if code in target_values:
                raise ValueError(
                    f"code {code!r} is already assigned to {new_id!r} on {platform}"
                )
        for code in selected_codes:
            values.remove(code)
            target_values.append(code)
            label = _take_label(old_entry, platform, code)
            if label is not None:
                _put_label(target_entry, platform, code, label)
            moves.append({"platform": platform, "code": code, "label": label})

    if created:
        items = _document_items(old_document)
        if items is None:
            raise ValueError(f"offer source {old_source!r} has no entry array")
        items.append(target_entry)

    remaining = sum(
        len(values)
        for values in old_product_ids.values()
        if isinstance(values, list)
    )
    deleted_old = False
    if delete_empty and remaining == 0:
        items = _document_items(old_document)
        if items is not None:
            del items[old_index]
            deleted_old = True

    return {
        "from": old_id,
        "to": new_id,
        "requestedFrom": old_app,
        "requestedTo": new_app,
        "created": created,
        "deletedOld": deleted_old,
        "moves": moves,
        "oldRemainingCodes": remaining,
        "sources": sorted({old_source, target_source}),
    }


def _normalize(values: Any) -> list[str]:
    """Trim, drop empties and remove duplicates while preserving order."""
    result: list[str] = []
    if not isinstance(values, (list, tuple)):
        return result
    for value in values:
        if not isinstance(value, str):
            continue
        text = value.strip()
        if text and text not in result:
            result.append(text)
    return result


def _find_entry(
    documents: list[tuple[str, Any]], app_id: str, *, allow_missing: bool
) -> tuple[str, Any, int, dict[str, Any]] | None:
    matches = _find_entries(documents, app_id)
    if len(matches) > 1:
        raise ValueError(
            f"application {app_id!r} has {len(matches)} offer entries; "
            "run offer-codes verify and fix the duplicates first"
        )
    if not matches:
        if allow_missing:
            return None
        raise ValueError(f"application {app_id!r} has no offer entry")
    entry = matches[0][3]
    entry_type = entry.get("type")
    if entry_type != "app":
        raise ValueError(
            f"{app_id!r} resolves to offer entry {_entry_id(entry) or '?'} of "
            f"type {entry_type!r}; only app entries can be maintained"
        )
    return matches[0]


def resolve_entry_id(
    documents: list[tuple[str, Any]], app: str
) -> str | None:
    """Return the app entry id for an id or alias, or None when absent."""
    requested = (app or "").strip()
    if not requested:
        return None
    match = _find_entry(documents, requested, allow_missing=True)
    return _entry_id(match[3]) if match else None


def _drop_label(entry: dict[str, Any], platform: str, code: str) -> None:
    variants = entry.get("variants")
    if not isinstance(variants, dict):
        return
    labels = variants.get(platform)
    if not isinstance(labels, dict):
        return
    labels.pop(code, None)
    if not labels:
        variants.pop(platform, None)
        if not variants:
            entry.pop("variants", None)


def find_codes(
    documents: list[tuple[str, Any]],
    *,
    apps: list[str] | None = None,
    codes: list[str] | None = None,
    platforms: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return one row per owned code, filtered by application, code or platform."""
    app_filter = set(_normalize(apps))
    code_filter = set(_normalize(codes))
    platform_filter = set(_normalize(platforms))
    rows: list[dict[str, Any]] = []

    for source, document in documents:
        for entry in _document_items(document) or []:
            if not isinstance(entry, dict):
                continue
            app_id = _entry_id(entry)
            if app_id is None:
                continue
            aliases = _normalize(entry.get("applications"))
            owners = {app_id, *aliases}
            if app_filter and not (owners & app_filter):
                continue
            product_ids = entry.get("productID")
            if not isinstance(product_ids, dict):
                continue
            for platform, values in product_ids.items():
                if platform_filter and str(platform) not in platform_filter:
                    continue
                if not isinstance(values, list):
                    continue
                variants = entry.get("variants")
                labels = (
                    variants.get(platform)
                    if isinstance(variants, dict)
                    and isinstance(variants.get(platform), dict)
                    else {}
                )
                for code in values:
                    if not isinstance(code, str) or not code.strip():
                        continue
                    if code_filter and code not in code_filter:
                        continue
                    rows.append(
                        {
                            "app": app_id,
                            "applications": sorted(owners),
                            "type": entry.get("type"),
                            "platform": str(platform),
                            "code": code,
                            "label": labels.get(code),
                            "source": source,
                        }
                    )

    rows.sort(key=lambda row: (row["app"], row["platform"], row["code"]))
    return rows


def add_codes(
    documents: list[tuple[str, Any]],
    *,
    app: str,
    platform: str,
    codes: list[str],
    labels: list[str] | None = None,
    allow_shared: bool = False,
) -> dict[str, Any]:
    """Add one or more codes to an application entry, creating it if needed.

    The original documents are updated only when the whole addition succeeds.
    A new entry is appended to the only source document; when the source has
    several files the caller must target one file explicitly.
    """
    working = copy.deepcopy(documents)
    plan = _add_codes(
        working,
        app=app,
        platform=platform,
        codes=codes,
        labels=labels,
        allow_shared=allow_shared,
    )
    _commit_documents(documents, working)
    return plan


def _add_codes(
    documents: list[tuple[str, Any]],
    *,
    app: str,
    platform: str,
    codes: list[str],
    labels: list[str] | None = None,
    allow_shared: bool = False,
) -> dict[str, Any]:
    """Add codes in a working copy; see :func:`add_codes`."""
    app = (app or "").strip()
    platform = (platform or "").strip()
    if not app:
        raise ValueError("--app must be a non-empty application id")
    if not platform:
        raise ValueError("--platform must be a non-empty platform key")
    if platform not in REQUIRED_PLATFORMS:
        raise ValueError(
            f"unknown platform {platform!r}; expected one of "
            + ", ".join(REQUIRED_PLATFORMS)
        )
    requested = _normalize(codes)
    if not requested:
        raise ValueError("at least one --code is required")

    match = _find_entry(documents, app, allow_missing=True)
    created = match is None
    if created:
        if len(documents) != 1:
            raise ValueError(
                "cannot choose a file for a new offer entry; "
                "pass --offer with a single JSON file"
            )
        source, document = documents[0][0], documents[0][1]
        items = _document_items(document)
        if items is None:
            raise ValueError(f"offer source {source!r} has no entry array")
        entry: dict[str, Any] = {
            "id": app,
            "productID": {key: [] for key in REQUIRED_PLATFORMS},
            "type": "app",
            "applications": [app],
        }
        items.append(entry)
    else:
        source, _document, _index, entry = match
        if not isinstance(entry.get("productID"), dict):
            raise ValueError(f"entry {app!r} has no productID object")

    entry_id = _entry_id(entry) or app
    label_map = _label_map(requested, labels)
    product_ids = entry["productID"]
    values = product_ids.setdefault(platform, [])
    if not isinstance(values, list):
        raise ValueError(f"productID.{platform} of {entry_id!r} is not an array")

    for code in requested:
        if code in values:
            raise ValueError(
                f"code {code!r} is already recorded for {entry_id!r} on {platform}"
            )
        for _other_source, other_document in documents:
            for other in _document_items(other_document) or []:
                other_id = _entry_id(other)
                if other_id is None:
                    continue
                other_ids = other.get("productID")
                other_values = (
                    other_ids.get(platform)
                    if isinstance(other_ids, dict)
                    else None
                )
                if isinstance(other_values, list) and code in other_values:
                    if allow_shared:
                        continue
                    raise ValueError(
                        f"code {code!r} is already assigned to {other_id!r} on "
                        f"{platform}; use offer-codes replace to move it, or "
                        "pass --allow-shared for a bundle listing"
                    )

    for code in requested:
        values.append(code)
        label = label_map.get(code)
        if label is not None:
            _put_label(entry, platform, code, label)

    return {
        "app": entry_id,
        "requestedApp": app,
        "platform": platform,
        "created": created,
        "added": [
            {"code": code, "label": label_map.get(code)} for code in requested
        ],
        "sources": [source],
    }


def _label_map(codes: list[str], labels: list[str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in labels or []:
        text = str(raw).strip()
        if not text:
            continue
        if "=" in text:
            code, label = text.split("=", 1)
            code, label = code.strip(), label.strip()
        else:
            if len(codes) != 1:
                raise ValueError(
                    "use --label CODE=LABEL when adding several codes"
                )
            code, label = codes[0], text
        if code not in codes:
            raise ValueError(f"label refers to code {code!r}, which is not being added")
        if not label:
            raise ValueError(f"label for {code!r} must not be empty")
        if any(not segment.strip() for segment in label.split(";")):
            raise ValueError(f"label for {code!r} has an empty ';'-separated segment")
        result[code] = label
    return result


def remove_codes(
    documents: list[tuple[str, Any]],
    *,
    app: str,
    platform: str,
    codes: list[str],
    delete_empty: bool = False,
) -> dict[str, Any]:
    """Remove codes and their labels from one application entry.

    The original documents are updated only when the whole removal succeeds.
    """
    working = copy.deepcopy(documents)
    plan = _remove_codes(
        working,
        app=app,
        platform=platform,
        codes=codes,
        delete_empty=delete_empty,
    )
    _commit_documents(documents, working)
    return plan


def _remove_codes(
    documents: list[tuple[str, Any]],
    *,
    app: str,
    platform: str,
    codes: list[str],
    delete_empty: bool = False,
) -> dict[str, Any]:
    """Remove codes in a working copy; see :func:`remove_codes`."""
    app = (app or "").strip()
    platform = (platform or "").strip()
    if not app:
        raise ValueError("--app must be a non-empty application id")
    if not platform:
        raise ValueError("--platform must be a non-empty platform key")
    requested = _normalize(codes)
    if not requested:
        raise ValueError("at least one --code is required")

    source, document, index, entry = _find_entry(
        documents, app, allow_missing=False
    )
    entry_id = _entry_id(entry) or app
    product_ids = entry.get("productID")
    if not isinstance(product_ids, dict):
        raise ValueError(f"entry {entry_id!r} has no productID object")
    values = product_ids.get(platform)
    if not isinstance(values, list):
        raise ValueError(
            f"application {entry_id!r} has no productID.{platform} array"
        )

    missing = [code for code in requested if code not in values]
    if missing:
        known = ", ".join(
            code for code in values if isinstance(code, str)
        ) or "none"
        raise ValueError(
            f"code(s) {', '.join(missing)} do not belong to {entry_id!r} on "
            f"{platform} (known: {known})"
        )

    for code in requested:
        product_ids[platform] = [
            value for value in product_ids[platform] if value != code
        ]
        _drop_label(entry, platform, code)

    remaining = sum(
        len(values)
        for values in product_ids.values()
        if isinstance(values, list)
    )
    deleted = False
    if delete_empty and remaining == 0:
        items = _document_items(document)
        if items is not None and index < len(items) and items[index] is entry:
            del items[index]
            deleted = True

    return {
        "app": entry_id,
        "requestedApp": app,
        "platform": platform,
        "removed": requested,
        "deletedApp": deleted,
        "remainingCodes": remaining,
        "sources": [source],
    }


def serialize_document(document: Any) -> str:
    """Return the repository's canonical JSON text for an offer document."""
    return json.dumps(document, indent=4, ensure_ascii=False) + "\n"


def write_documents(
    documents: list[tuple[str, Any]], sources: list[str],
    *, expected: dict[str, str] | None = None,
) -> None:
    """Rewrite the selected local offer documents with the repository JSON style.

    Every changed document is first written to a temporary file next to the
    target, then moved into place with ``os.replace``. If preparing a temporary
    file fails, the original files are left untouched.
    """
    wanted = set(sources)
    pending: list[tuple[str, Path]] = []
    backups: dict[Path, str] = {}
    committed: list[Path] = []
    retained: set[str] = set()

    def check_snapshot():
        for source, before in (expected or {}).items():
            if Path(source).read_text(encoding="utf-8") != before:
                raise ValueError(f"offer source changed since it was read: {source}; retry")

    def stage(path: Path, content: bytes) -> str:
        descriptor, name = tempfile.mkstemp(
            dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
        )
        pending.append((name, path))
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(name, stat.S_IMODE(path.stat().st_mode))
        return name

    try:
        check_snapshot()
        updates: list[tuple[str, Path]] = []
        for source, document in documents:
            if source not in wanted:
                continue
            path = Path(source)
            backups[path] = stage(path, path.read_bytes())
            updates.append((stage(path, serialize_document(document).encode("utf-8")), path))
        check_snapshot()
        for temp_name, path in updates:
            os.replace(temp_name, path)
            committed.append(path)
    except BaseException:
        failed = []
        for path in reversed(committed):
            try:
                os.replace(backups[path], path)
            except OSError:
                retained.add(backups[path])
                failed.append(f"{path} (original preserved at {backups[path]})")
        if failed:
            raise OSError("write failed and rollback was incomplete: " + "; ".join(failed)) from None
        raise
    finally:
        for temp_name, _path in pending:
            if temp_name not in retained:
                Path(temp_name).unlink(missing_ok=True)


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
