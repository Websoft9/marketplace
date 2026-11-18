"""Validated OS vocabulary shared by offer rendering, resolution and app matching."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .marketplace import CONFIG_PATH

CATALOG_PATH = CONFIG_PATH.parent / "os-catalog.json"


def normalize_name(value: str) -> str:
    return " ".join(value.casefold().split())


def load_catalog(path: Path = CATALOG_PATH) -> dict:
    path = Path(path)

    def invalid(message):
        raise ValueError(f"invalid OS catalog {path}: {message}")

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        invalid("file is missing, unreadable or not valid JSON")
    if not isinstance(document, dict):
        invalid("root must be an object")
    if set(document) - {"version", "versionPattern", "versionPatternScope", "families"}:
        invalid("unknown top-level field")
    if type(document.get("version")) is not int or document["version"] != 1:
        invalid("version must be 1")
    if document.get("versionPatternScope") != "immediately-after-matched-os-name":
        invalid("unsupported versionPatternScope")
    expression = document.get("versionPattern")
    if not isinstance(expression, str) or not expression:
        invalid("versionPattern must be a non-empty regex string")
    try:
        pattern = re.compile(expression)
    except re.error:
        invalid("versionPattern is not a valid regex")
    if pattern.fullmatch(""):
        invalid("versionPattern must not match an empty version")
    families = document.get("families")
    if not isinstance(families, list) or not families:
        invalid("families must be a non-empty array")
    ids = set()
    all_aliases = set()
    for family in families:
        if not isinstance(family, dict):
            invalid("each family must be an object")
        if set(family) - {"id", "display", "aliases", "versionExamples"}:
            invalid("unknown family field")
        family_id = family.get("id")
        if not isinstance(family_id, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", family_id):
            invalid("family id must be a non-empty lowercase slug")
        if family_id in ids:
            invalid("duplicate family id")
        ids.add(family_id)
        display = family.get("display")
        if not isinstance(display, str) or not display.strip() or display != display.strip():
            invalid("family display must be a non-empty trimmed string")
        aliases = family.get("aliases")
        if not isinstance(aliases, list) or not aliases:
            invalid("family aliases must be a non-empty array")
        normalized = []
        for alias in aliases:
            if not isinstance(alias, str) or not alias.strip() or alias != alias.strip():
                invalid("aliases must be non-empty trimmed strings")
            name = normalize_name(alias)
            if name in all_aliases:
                invalid("duplicate alias after case/whitespace normalization")
            all_aliases.add(name)
            normalized.append(name)
        if normalize_name(display) not in normalized:
            invalid("family display must also be declared as an alias")
        examples = family.get("versionExamples", [])
        if not isinstance(examples, list):
            invalid("versionExamples must be an array of version strings")
        seen_versions = set()
        for example in examples:
            if not isinstance(example, str) or not pattern.fullmatch(example):
                invalid("versionExamples must contain strings matching versionPattern")
            if example in seen_versions:
                invalid("duplicate version example")
            seen_versions.add(example)
    return document


def alias_maps(catalog):
    """Preserve the compatibility constants without maintaining any OS list in code."""
    display = {normalize_name(alias): family["display"]
               for family in catalog["families"] for alias in family["aliases"]}
    return tuple(display), display


# Like marketplace.json, read and validate once per CLI process. Bad or missing
# configuration fails closed; never silently revert to a second hardcoded list.
CATALOG = load_catalog()
OS_NAMES, OS_DISPLAY = alias_maps(CATALOG)
