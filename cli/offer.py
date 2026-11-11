"""Deterministic AWS listing rendering, validation and change planning."""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
import re
import urllib.error
from pathlib import Path
from typing import Any

from .aws_marketplace import (
    CONTENT_FIELDS as FIELDS,
    FIELD_LIMITS as LIMITS,
    aws_call,
    aws_client,
    build_update_request,
    change_status,
    current_content,
)
from .marketplace import (
    CATALOG_URL,
    OFFER_SOURCE,
    REPO_ROOT,
    load_json,
    offer_codes_by_app,
    offer_variants_by_app,
)

ROOT = REPO_ROOT
DATA = ROOT / "data" / "listing"
SPECIAL_CASES = DATA / "app-special-cases.json"
SPECIAL_CASE_SLOTS = {
    "editionLabel": "text",
    "pricingLead": "text",
    "titleTrademark": "text",
    "multiVersion": "flag",
}
DEFAULT_EDITION_LABEL = "Community Edition"
OS_NAMES = (
    "amazon linux",
    "oracle linux",
    "red hat",
    "rocky linux",
    "almalinux",
    "alma linux",
    "centos",
    "debian",
    "ubuntu",
    "suse",
    "sles",
    "fedora",
    "windows server",
    "freebsd",
    "rhel",
)
PLATFORM_PHRASE = " on AWS"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


PLACEHOLDER = re.compile(r"\{([^{}]+)\}")
COMMERCIAL_VERSION = re.compile(r"enterprise|(?<![a-z0-9])ee(?![a-z0-9])", re.IGNORECASE)


def resolve_placeholder(path: str, roots: dict[str, Any]) -> Any:
    normalized = re.sub(r"\[(\d+)\]", r".\1", path)
    current: Any = roots
    for part in normalized.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            raise ValueError(f"template placeholder not found: {path}")
    return current


def _find_outside(text: str, target: str) -> int:
    quote = None
    escaped = False
    for index, character in enumerate(text):
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if quote:
            if character == quote:
                quote = None
            continue
        if character in "'\"":
            quote = character
        elif character == target:
            return index
    return -1


def _split_outside(text: str, separator: str) -> list[str]:
    parts: list[str] = []
    start = 0
    while True:
        index = _find_outside(text[start:], separator)
        if index == -1:
            parts.append(text[start:])
            return parts
        index += start
        parts.append(text[start:index])
        start = index + len(separator)


def _unquote(text: str) -> str:
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1]
    return text


def _evaluate_operand(text: str, roots: dict[str, Any]) -> Any:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return _unquote(text)
    return evaluate_expression(text, roots)


def evaluate_expression(expression: str, roots: dict[str, Any]) -> Any:
    expression = expression.strip()
    question = _find_outside(expression, "?")
    if question != -1:
        condition = expression[:question].strip()
        remainder = expression[question + 1:]
        colon = _find_outside(remainder, ":")
        if colon == -1:
            raise ValueError(f"invalid conditional template expression: {expression}")
        return (
            _evaluate_operand(remainder[:colon], roots)
            if evaluate_expression(condition, roots)
            else _evaluate_operand(remainder[colon + 1:], roots)
        )
    parts = _split_outside(expression, "|")
    value = resolve_placeholder(parts[0].strip(), roots)
    for filter_expression in parts[1:]:
        name, _, argument = filter_expression.partition(":")
        if name.strip() != "join":
            raise ValueError(f"unsupported template filter: {name.strip()}")
        if not isinstance(value, list):
            raise ValueError("join filter requires an array value")
        arguments = [_unquote(item.strip()) for item in _split_outside(argument, ",")]
        separator = arguments[0] if arguments else ", "
        suffix = arguments[1] if len(arguments) > 1 else ""
        joined = separator.join(str(item) for item in value)
        value = joined + suffix if joined else joined
    return value


def format_template(text: str, roots: dict[str, Any]) -> Any:
    match = PLACEHOLDER.fullmatch(text.strip())
    if match:
        return evaluate_expression(match.group(1), roots)
    return PLACEHOLDER.sub(lambda item: str(evaluate_expression(item.group(1), roots)), text)


def is_community_version(value: str) -> bool:
    """Return whether a version belongs to the offer's open-source edition.

    Version sources may list commercial builds next to community builds, for
    example ``5.26-enterprise`` or ``5.26-ee``. Those must not be advertised as
    selectable versions of the open-source offer.
    """
    return not COMMERCIAL_VERSION.search(str(value))


def display_version(value: str) -> str:
    """Keep at most two dotted components for listing display."""
    text = str(value).strip()
    parts = text.split(".")
    return ".".join(parts[:2]) if len(parts) > 2 else text


def version_choices(options: list[str]) -> str:
    values = [display_version(item) for item in options]
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} or {values[1]}"
    return ", ".join(values[:-1]) + f", or {values[-1]}"


def load_special_cases(path: Path = SPECIAL_CASES) -> dict[str, dict[str, str | bool]]:
    """Read per-application presentation exceptions from the repository.

    The file is optional: without it every application uses the template
    defaults. Unknown slots or malformed values fail instead of being ignored.
    """
    if not path.is_file():
        return {}
    data = read_json(path)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    if data.get("schemaVersion") != 1:
        raise ValueError(f"{path} has an unsupported schemaVersion")
    apps = data.get("apps") or {}
    if not isinstance(apps, dict):
        raise ValueError(f"{path} apps must be an object keyed by application id")
    cases: dict[str, dict[str, str | bool]] = {}
    for app_id, entry in apps.items():
        if not isinstance(app_id, str) or not app_id.strip() or app_id != app_id.strip():
            raise ValueError(f"{path} has an invalid application id")
        if not isinstance(entry, dict):
            raise ValueError(f"{path} entry {app_id!r} must be an object")
        unknown = set(entry) - set(SPECIAL_CASE_SLOTS)
        if unknown:
            raise ValueError(
                f"{path} entry {app_id!r} uses unsupported key(s): {', '.join(sorted(unknown))}"
            )
        for key, value in entry.items():
            kind = SPECIAL_CASE_SLOTS[key]
            if kind == "flag":
                if not isinstance(value, bool):
                    raise ValueError(f"{path} entry {app_id!r} {key} must be a boolean")
                continue
            if not isinstance(value, str):
                raise ValueError(f"{path} entry {app_id!r} {key} must be a string")
            if value != value.strip():
                raise ValueError(f"{path} entry {app_id!r} {key} has surrounding whitespace")
        for key in ("pricingLead", "titleTrademark"):
            if key in entry and not entry[key]:
                raise ValueError(f"{path} entry {app_id!r} {key} must not be empty")
        cases[app_id] = dict(entry)
    return cases


def edition_values(catalog_item: dict, special_cases: dict[str, dict[str, str | bool]]) -> dict:
    """Resolve the edition label and pricing sentence for one application."""
    entry = special_cases.get(catalog_item.get("id")) or {}
    default_label = DEFAULT_EDITION_LABEL if catalog_item.get("hasenterprise") is True else ""
    label = entry.get("editionLabel", default_label)
    has_label = bool(label)
    trademark = str(catalog_item.get("trademark") or "").strip()
    if has_label:
        subject = f"{trademark} {label}" if trademark else f"the {label} edition"
        default_pricing = (
            f"This offer provides {subject}."
            " It does not include any commercial edition."
        )
    else:
        default_pricing = "No separate license fee is charged for the application."
    return {
        "label": label,
        "labelSpaced": f" {label}" if has_label else "",
        "hasLabel": has_label,
        "pricingLead": entry.get("pricingLead") or default_pricing,
    }


def branding_values(catalog_item: dict, special_cases: dict[str, dict[str, str | bool]]) -> dict:
    """Resolve the trademark used in the AWS product title."""
    entry = special_cases.get(catalog_item.get("id")) or {}
    trademark = str(catalog_item.get("trademark") or "").strip()
    return {"titleTrademark": str(entry.get("titleTrademark") or trademark)}


def is_os_label(label: str) -> bool:
    """Treat a label as an operating system name when it starts with a known OS."""
    value = " ".join(str(label).casefold().split())
    return any(value == name or value.startswith(name + " ") for name in OS_NAMES)


def split_offer_label(label: str) -> tuple[str, list[str]]:
    """Split a semicolon-delimited label into its OS label and the others."""
    parts = [part.strip() for part in str(label or "").split(";") if part.strip()]
    os_label = next((part for part in parts if is_os_label(part)), "")
    others = [part for part in parts if part != os_label] if os_label else parts
    return os_label, others


def variant_title(title: str, trademark: str, label: str) -> str:
    """Apply offer-code labels to a rendered product title.

    A non-OS label is inserted after the trademark; an OS label replaces the
    `` on AWS`` platform phrase with `` on <OS>``.
    """
    os_label, others = split_offer_label(label)
    result = title
    if others:
        if not trademark or trademark not in result:
            raise ValueError(f"product title does not contain trademark {trademark!r}")
        result = result.replace(
            trademark, trademark + "".join(f" {item}" for item in others), 1
        )
    if os_label:
        if PLATFORM_PHRASE not in result:
            raise ValueError(f"product title does not contain {PLATFORM_PHRASE.strip()!r}")
        result = result.replace(PLATFORM_PHRASE, f" on {os_label}", 1)
    return result


def with_variant(bundle: dict, label: str, strict: bool = True) -> dict:
    """Return a bundle whose ProductTitle reflects one offer-code label.

    ``strict`` rejects an over-limit title. Build previews pass ``strict=False``
    so the title can be reported as a warning instead of blocking the file.
    """
    label = str(label or "").strip()
    if not label:
        return bundle
    content = dict(bundle["content"])
    title = variant_title(
        str(content.get("ProductTitle", "")),
        str(bundle.get("titleTrademark") or bundle.get("trademark") or ""),
        label,
    )
    warnings = []
    if len(title) > LIMITS["ProductTitle"]:
        if strict:
            raise ValueError(
                f"ProductTitle is {len(title)} characters, "
                f"maximum {LIMITS['ProductTitle']}"
            )
        warnings.append(
            {
                "field": "ProductTitle",
                "length": len(title),
                "max": LIMITS["ProductTitle"],
                "title": title,
            }
        )
    content["ProductTitle"] = title
    preview = copy.deepcopy(bundle.get("preview") or {})
    if isinstance(preview.get("content"), dict):
        preview["content"]["ProductTitle"] = title
    result = {
        **bundle,
        "content": content,
        "preview": preview,
        "variantLabel": label,
        "sourceHash": digest({"base": bundle["sourceHash"], "variant": label}),
    }
    if warnings:
        result["titleWarnings"] = warnings
    return result


def variant_labels(
    app: str,
    cloud: str,
    offer_source: str = OFFER_SOURCE,
    timeout: float = 30,
) -> dict[str, str]:
    """Return code-to-label mappings for one application and cloud."""
    try:
        variants = offer_variants_by_app(offer_source, timeout)
    except (OSError, ValueError, urllib.error.URLError) as error:
        raise ValueError(f"failed to read offer labels: {error}") from None
    return variants.get(app, {}).get(cloud, {})



def catalog_app_ids(source: str = CATALOG_URL, timeout: float = 30) -> list[str]:
    """Return the application ids from product_en.json in catalog order."""
    data = load_json(source, timeout)
    if not isinstance(data, list):
        raise ValueError("application catalog must be a JSON array")
    keys: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(data):
        key = item.get("key") if isinstance(item, dict) else None
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"application catalog item {index} has no string key")
        key = key.strip()
        if key in seen:
            raise ValueError(f"duplicate application key in application catalog: {key}")
        seen.add(key)
        keys.append(key)
    if not keys:
        raise ValueError("application catalog contains no applications")
    return keys


def normalize_app_id(value: str, available: list[str]) -> str:
    """Validate an application id and suggest corrections instead of guessing."""
    app = (value or "").strip()
    if not app:
        raise ValueError("application id is required")
    if re.search(r"\s", app):
        raise ValueError(f"application id must not contain whitespace: {value!r}")
    if app in available:
        return app
    lowered = app.casefold()
    if lowered in available:
        raise ValueError(f"application id is case-sensitive; use {lowered!r} instead of {app!r}")
    matches = difflib.get_close_matches(lowered, available, n=3, cutoff=0.6)
    hint = f"; did you mean {', '.join(repr(match) for match in matches)}?" if matches else ""
    raise ValueError(f"application {app!r} is not in the application catalog{hint}")


def offer_codes_for(
    app: str,
    cloud: str,
    offer_codes: list[str] | None = None,
    offer_source: str = OFFER_SOURCE,
    timeout: float = 30,
) -> tuple[list[str], str]:
    """Return the offer codes for one application and cloud, with their origin."""
    by_app = offer_codes_by_app(offer_source, timeout)
    available = [str(code).strip() for code in ((by_app.get(app) or {}).get(cloud) or []) if str(code).strip()]
    requested = [str(code).strip() for code in (offer_codes or []) if str(code).strip()]
    requested = list(dict.fromkeys(requested))
    if requested:
        unknown = [code for code in requested if code not in available]
        if unknown:
            known = ", ".join(available) if available else "none"
            raise ValueError(
                f"offer code(s) {', '.join(unknown)} do not belong to {app!r} on {cloud} (known: {known})"
            )
        return requested, "explicit"
    if not available:
        raise ValueError(f"{app!r} has no {cloud} offer code in the offer source")
    return available, "catalog"


def preflight(
    app: str,
    cloud: str,
    offer_codes: list[str] | None = None,
    catalog_source: str = CATALOG_URL,
    offer_source: str = OFFER_SOURCE,
    timeout: float = 30,
) -> dict:
    """Validate the application id and offer codes without touching any cloud API."""
    app = normalize_app_id(app, catalog_app_ids(catalog_source, timeout))
    codes, code_source = offer_codes_for(app, cloud, offer_codes, offer_source, timeout)
    return {
        "schemaVersion": 1,
        "app": app,
        "cloud": cloud,
        "codes": codes,
        "codeSource": code_source,
    }


def listing_targets(catalog_item: dict) -> list[dict]:
    """Read the per-entity targets recorded in a catalog item."""
    raw = catalog_item.get("targets")
    targets: list[dict] = []
    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                raise ValueError("catalog targets must be objects with code and entityId")
            entity_id = entry.get("entityId")
            if not isinstance(entity_id, str) or not entity_id.strip():
                raise ValueError("catalog target requires a non-empty entityId")
            targets.append({"code": entry.get("code") or None, "entityId": entity_id.strip()})
    if not targets and catalog_item.get("entityId"):
        targets.append({"code": None, "entityId": str(catalog_item["entityId"])})
    return targets


def render(catalog_item: dict, version: str, vendor_path: Path = DATA / "vendor.json", template_path: Path = DATA / "templates/aws-ami.json", version_options: list[str] | None = None, special_cases_path: Path = SPECIAL_CASES) -> dict:
    vendor, template = read_json(vendor_path), read_json(template_path)
    special_cases = load_special_cases(special_cases_path)
    options = [str(item).strip() for item in (version_options or [])]
    options = [
        display_version(item)
        for item in options
        if item and item.casefold() != "latest" and is_community_version(item)
    ]
    options = list(dict.fromkeys(options))
    multi_version = bool((special_cases.get(catalog_item.get("id")) or {}).get("multiVersion")) and len(options) >= 2
    selection = f" {'/'.join(options)} for selection" if multi_version else ""
    short_choices = version_choices(options)
    displayed_version = display_version(version) if version else ""
    # Includes its leading space so a missing version does not leave a double space.
    version_choice = (
        f" {short_choices}" if multi_version else (f" {displayed_version}" if displayed_version else "")
    )
    namespaces = template.get("namespaces")
    if not isinstance(namespaces, list) or not namespaces or any(not isinstance(namespace, str) or not namespace for namespace in namespaces):
        raise ValueError("template.namespaces must contain at least one namespace")
    template_id = catalog_item.get("templateId") or "aws-ami-v1"
    if template.get("templateId") != template_id or template.get("schemaVersion") != 1:
        raise ValueError("unsupported or mismatched template")
    entity_type = catalog_item.get("entityType") or "AmiProduct@1.0"
    vendor_fields = (
        "aboutVendor",
        "billingSummary",
        "consoleSummary",
        "sshSummary",
        "supportSummary",
        "supportContact",
        "supportScope",
        "trademarkStatement",
    )
    for field in vendor_fields:
        if not isinstance(vendor.get(field), str) or not vendor[field].strip():
            raise ValueError(f"vendor.{field} must be non-empty text")
    highlights = vendor.get("highlights")
    if not isinstance(highlights, list) or not 1 <= len(highlights) <= 3 or any(
        not isinstance(item, str) or not item.strip() for item in highlights
    ):
        raise ValueError("vendor.highlights must contain 1–3 non-empty strings")
    catalog_values = dict(catalog_item)
    if not isinstance(catalog_values.get("hasenterprise"), bool):
        catalog_values["hasenterprise"] = True
    editions = edition_values(catalog_values, special_cases)
    branding = branding_values(catalog_values, special_cases)
    available = {
        "catalog": catalog_values,
        "branding": branding,
        "versions": {
            "id": catalog_item.get("id"),
            "version": displayed_version,
            "choice": version_choice,
            "selection": selection,
            "multi": multi_version,
            "shortChoices": short_choices,
        },
        "vendor": vendor,
        "editions": editions,
    }
    roots = {}
    for namespace in namespaces:
        if namespace not in available:
            raise ValueError(f"template namespace has no source: {namespace}")
        roots[namespace] = available[namespace]
    target = template.get("target") or {}
    if target:
        if target.get("platform") not in (None, "aws"):
            raise ValueError("template target.platform must be aws")
        if target.get("entityType") not in (None, entity_type):
            raise ValueError("template target.entityType does not match the listing")
        if target.get("changeType") not in (None, "UpdateInformation"):
            raise ValueError("template target.changeType must be UpdateInformation")
    content = {key: [format_template(item, roots) for item in value] if isinstance(value, list) else format_template(value, roots) for key, value in template["content"].items()}
    rendered_sections = [
        {
            "heading": format_template(section["heading"], roots),
            "text": format_template(section["text"], roots),
        }
        for section in template["longDescriptionSections"]
    ]
    heading_format = "**{}**" if template.get("boldLongDescriptionHeadings") else "{}"
    content["LongDescription"] = "\n\n".join(
        (heading_format.format(section["heading"]) + "\n\n" if section["heading"] else "") + section["text"]
        for section in rendered_sections
    )
    preview_only_template = template.get("previewOnlySections") or []
    if not isinstance(preview_only_template, list):
        raise ValueError("template.previewOnlySections must be an array")
    preview_only_sections = []
    for section in preview_only_template:
        if not isinstance(section, dict) or set(section) != {"id", "heading", "text"}:
            raise ValueError("template.previewOnlySections items require id, heading and text")
        preview_only_sections.append(
            {
                "id": format_template(section["id"], roots),
                "heading": format_template(section["heading"], roots),
                "text": format_template(section["text"], roots),
            }
        )
    for field in ("Categories", "VideoUrls"):
        if not content.get(field):
            content.pop(field, None)
    if not content.get("LogoUrl"):
        content.pop("LogoUrl", None)
    resources = template.get("AdditionalResources", template.get("additionalResources")) or []
    if resources:
        content["AdditionalResources"] = [
            {key: format_template(str(value), roots) for key, value in item.items()} for item in resources
        ]
    content.update(catalog_item.get("contentOverrides") or {})
    # Absent optional fields preserve the current AWS value. Empty arrays never clear it implicitly.
    validate_content(content)
    blockers = []
    if not version:
        blockers.append("versions: version is missing")
    entity_id = catalog_item.get("entityId")
    targets = listing_targets(catalog_item)
    if not targets:
        blockers.append("listing: Catalog EntityId is missing")
    preview_content = {
        key: value
        for key, value in content.items()
        if key not in {"LongDescription", "AdditionalResources"}
    }
    preview = {
        "schemaVersion": template["schemaVersion"],
        "templateId": template["templateId"],
        "target": template.get("target", {}),
        "AdditionalResources": content.get("AdditionalResources", []),
        "content": preview_content,
        "longDescriptionSections": rendered_sections,
        "previewOnlySections": preview_only_sections,
    }
    return {
        "schemaVersion": 1, "key": catalog_item["id"], "entityId": entity_id, "entityType": entity_type,
        "templateId": template["templateId"], "content": content, "preview": preview,
        "targets": targets,
        "trademark": catalog_item.get("trademark") or "",
        "titleTrademark": branding["titleTrademark"],
        "offerCodes": catalog_item.get("offerCodes") or {},
        "publishBlockers": blockers,
        "sourceHash": digest({"catalog": catalog_item, "version": version, "vendor": vendor, "template": template, "specialCases": special_cases.get(catalog_item["id"])}),
    }


def validate_content(content: dict) -> None:
    if not content or set(content) - FIELDS:
        raise ValueError("unsupported or empty UpdateInformation content")
    for field, maximum in LIMITS.items():
        value = content.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise ValueError(f"{field} must contain 1–{maximum} characters")
    for field, maximum, item_max in (("Highlights", 3, 500), ("SearchKeywords", 15, 50), ("Categories", 3, None)):
        if field not in content:
            continue
        values = content[field]
        if not isinstance(values, list) or not 1 <= len(values) <= maximum or any(not isinstance(item, str) or not item.strip() or (item_max and len(item) > item_max) for item in values):
            raise ValueError(f"invalid {field}")
        if len({item.casefold() for item in values}) != len(values):
            raise ValueError(f"duplicate {field}")
    if sum(map(len, content["SearchKeywords"])) > 250:
        raise ValueError("SearchKeywords exceeds 250 combined characters")
    for field in ("LogoUrl", "VideoUrls", "AdditionalResources"):
        if field not in content:
            continue
        value = content[field]
        if field == "LogoUrl":
            urls = [value]
        elif field == "VideoUrls":
            if not isinstance(value, list) or len(value) > 1:
                raise ValueError("VideoUrls supports at most one URL")
            urls = value
        else:
            if not isinstance(value, list) or len(value) > 3 or any(not isinstance(item, dict) or set(item) != {"Text", "Url"} or not isinstance(item["Text"], str) or not item["Text"].strip() for item in value):
                raise ValueError("invalid AdditionalResources")
            urls = [item["Url"] for item in value]
        if any(not isinstance(url, str) or not re.fullmatch(r"https://[^\s?#]+", url) for url in urls):
            raise ValueError(f"{field} requires unsigned HTTPS URLs")
    def text_values(value, path):
        if isinstance(value, str):
            yield path, value
        elif isinstance(value, list):
            for index, item in enumerate(value):
                yield from text_values(item, f"{path}[{index}]")
        elif isinstance(value, dict):
            for key, item in value.items():
                yield from text_values(item, f"{path}.{key}")

    values = list(text_values(content, "content"))

    def first_match(predicate):
        return next(((path, value) for path, value in values if predicate(value)), None)

    match = first_match(lambda value: re.search(r"[\x00-\x08\x0b-\x1f]", value))
    if match:
        path, value = match
        raise ValueError(f"{path} contains unsupported control characters: {value[:120]!r}")
    match = first_match(lambda value: "  " in value)
    if match:
        path, value = match
        raise ValueError(f"{path} contains consecutive spaces: {value[:120]!r}")
    match = first_match(lambda value: re.search(r"\{[A-Za-z][A-Za-z0-9_]*\}", value))
    if match:
        path, value = match
        raise ValueError(f"{path} contains unresolved template placeholders: {value[:120]!r}")


def plan(bundle: dict, entity: dict) -> dict:
    identifier = entity.get("EntityIdentifier", "")
    if not bundle["entityId"] or identifier.split("@")[0] != bundle["entityId"] or "@" not in identifier:
        raise ValueError("snapshot EntityIdentifier does not match the master Catalog EntityId/revision")
    current = current_content(entity)
    changes = {key: value for key, value in bundle["content"].items() if current.get(key) != value}
    request = build_update_request(bundle, identifier, changes)
    result = {"schemaVersion": 1, "key": bundle["key"], "sourceHash": bundle["sourceHash"], "baseRevision": identifier, "currentHash": digest(current), "publishBlockers": bundle["publishBlockers"], "diff": {key: {"before": current.get(key), "after": value} for key, value in changes.items()}, "request": request}
    result["planHash"] = digest(result)
    return result


def multi_plan(bundle: dict, entities: list[dict], labels: dict[str, str] | None = None) -> dict:
    """Plan one content render against several Catalog entities.

    Each entry in ``entities`` carries its own ``code``, ``entityId`` and the
    ``describe_entity`` response. A code with a display label gets its own
    ProductTitle, so every target plan stays bound to its own EntityId,
    revision and offer-code label.
    """
    labels = labels or {}
    targets = []
    seen_entities: set[str] = set()
    for entry in entities:
        entity_id = entry["entityId"]
        if entity_id in seen_entities:
            raise ValueError(
                f"duplicate Catalog entity in plan targets: {entity_id}; "
                "each entity must be submitted once"
            )
        seen_entities.add(entity_id)
        target_bundle = with_variant(
            {**bundle, "entityId": entity_id}, labels.get(entry.get("code"), "")
        )
        target_plan = plan(target_bundle, entry["entity"])
        targets.append(
            {
                "code": entry.get("code"),
                "entityId": entity_id,
                "plan": target_plan,
            }
        )
    return {
        "schemaVersion": 1,
        "key": bundle["key"],
        "sourceHash": digest([target["plan"]["sourceHash"] for target in targets]),
        "publishBlockers": bundle["publishBlockers"],
        "targets": targets,
        "planHash": digest(targets),
    }
