#!/usr/bin/env python3
"""Validate the marketplace-operations-check skill and its platform references."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


SKILL_DIR = Path(".agents/skills/marketplace-operations-check")
CONFIG = Path("config/marketplace.json")
MATRIX = Path("data/offer-codes/matrix.json")
REGISTRY_ROW = re.compile(r"^\|\s*`([a-z0-9_-]+)`\s*\|")
CHECKS_INPUT = re.compile(r"Subset of `([a-z0-9_,]+)`")
LISTING_URL = re.compile(r"Listing URL:\s*`([^`]+)`")


def read_frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not match:
        return {}
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
    return fields


def parse_registry(text: str) -> set[str]:
    names = set()
    marker = "## Check registry"
    if marker not in text:
        return names
    section = text.split(marker, 1)[1].split("\n## ", 1)[0]
    for line in section.splitlines():
        match = REGISTRY_ROW.match(line)
        if match:
            names.add(match.group(1))
    return names


def parse_checks_input(text: str) -> set[str]:
    match = CHECKS_INPUT.search(text)
    if not match:
        return set()
    return {value.strip() for value in match.group(1).split(",") if value.strip()}


def validate(root: Path) -> list[str]:
    errors: list[str] = []
    skill_dir = root / SKILL_DIR
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.is_file():
        return [f"missing {skill_file.relative_to(root)}"]

    text = skill_file.read_text(encoding="utf-8")
    fields = read_frontmatter(text)
    if fields.get("name") != skill_dir.name:
        errors.append(
            f"frontmatter name {fields.get('name')!r} does not match directory "
            f"{skill_dir.name!r}"
        )
    if not fields.get("description"):
        errors.append("frontmatter description is empty")

    checks_dir = skill_dir / "references/checks"
    check_files = {path.stem for path in checks_dir.glob("*.md")} if checks_dir.is_dir() else set()
    registry = parse_registry(text)
    if not registry:
        errors.append("check registry table in SKILL.md has no rows")
    if registry != check_files:
        errors.append(
            "check registry and references/checks differ: "
            f"only in registry={sorted(registry - check_files)}, "
            f"only in files={sorted(check_files - registry)}"
        )

    checks_input = parse_checks_input(text)
    if checks_input != check_files:
        errors.append(
            "checks input contract and references/checks differ: "
            f"only in contract={sorted(checks_input - check_files)}, "
            f"only in files={sorted(check_files - checks_input)}"
        )

    config_file = root / CONFIG
    if not config_file.is_file():
        errors.append(f"missing {CONFIG}")
        platforms: dict[str, dict] = {}
    else:
        platforms = {
            entry["key"]: entry
            for entry in json.loads(config_file.read_text(encoding="utf-8"))["platforms"]
        }

    platforms_dir = skill_dir / "references/platforms"
    for path in sorted(platforms_dir.glob("*.md")) if platforms_dir.is_dir() else []:
        if path.stem == "_template":
            continue
        if path.stem not in platforms:
            errors.append(
                f"platform reference {path.name} has no key in {CONFIG}"
            )
            continue
        documented = LISTING_URL.search(path.read_text(encoding="utf-8"))
        configured = platforms[path.stem].get("urlTemplate")
        if documented and configured and documented.group(1) != configured:
            errors.append(
                f"platform reference {path.name} URL {documented.group(1)!r} differs "
                f"from configured {configured!r}"
            )
        elif documented and not configured:
            errors.append(
                f"platform reference {path.name} documents a URL but {CONFIG} "
                f"has no urlTemplate for {path.stem!r}"
            )

    matrix_file = root / MATRIX
    if not matrix_file.is_file():
        errors.append(f"missing {MATRIX}")
    elif platforms:
        for entry in json.loads(matrix_file.read_text(encoding="utf-8")):
            for key in entry.get("productID", {}):
                if key not in platforms:
                    errors.append(
                        f"{MATRIX} entry {entry.get('id')!r} uses platform {key!r} "
                        f"that is not in {CONFIG}"
                    )

    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--json", action="store_true", help="print machine-readable output")
    args = parser.parse_args()

    errors = validate(args.root.resolve())
    if args.json:
        print(json.dumps({"ok": not errors, "errors": errors}, indent=2))
    else:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        if not errors:
            print("marketplace-operations-check references are consistent")
    raise SystemExit(1 if errors else 0)


if __name__ == "__main__":
    main()
