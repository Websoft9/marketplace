#!/usr/bin/env python3
"""Resolve offer codes for selected applications and report local conflicts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


PATTERNS = {
    "aws": re.compile(r"^[a-z0-9]{20,32}$"),
    "aliyun": re.compile(r"^cmjj\d+$"),
    "alibabacloud": re.compile(r"^sgcmjj\d+$"),
    "azure": re.compile(r"^[a-z0-9-]+\.[a-z0-9-]+$"),
    "azure-vmlab": re.compile(r"^[a-z0-9-]+\.[a-z0-9-]+$"),
    "huaweiyun": re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    ),
    "huaweicloud": re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    ),
}


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def matches(entry: dict, app: str) -> bool:
    return app == entry.get("id") or app in entry.get("applications", [])


def analyze(matrix: list[dict], platforms: dict[str, dict], apps: list[str], selected: list[str]) -> dict:
    owners: dict[tuple[str, str], list[str]] = {}
    for entry in matrix:
        for platform, codes in entry.get("productID", {}).items():
            for code in codes:
                owners.setdefault((platform, code), []).append(entry.get("id"))

    rows: list[dict] = []
    missing: list[dict] = []
    multi_code: list[dict] = []
    drift: list[dict] = []
    conflicts: list[dict] = []
    in_scope: set[str] = set()

    for app in apps:
        entry = next((item for item in matrix if matches(item, app)), None)
        if entry is None:
            continue
        in_scope.add(entry.get("id"))
        for platform in selected:
            codes = entry.get("productID", {}).get(platform, [])
            if not codes:
                missing.append({"platform": platform, "app": app})
                continue
            if len(codes) > 1:
                multi_code.append({"platform": platform, "app": app, "codes": codes})
            for code in codes:
                pattern = PATTERNS.get(platform)
                if pattern and not pattern.match(code):
                    drift.append({"platform": platform, "app": app, "code": code})
                template = platforms.get(platform, {}).get("urlTemplate")
                rows.append(
                    {
                        "platform": platform,
                        "app": app,
                        "code": code,
                        "variant": entry.get("variants", {}).get(platform, {}).get(code, ""),
                        "url": template.replace("{code}", code) if template else None,
                    }
                )

    for (platform, code), entry_ids in sorted(owners.items()):
        if platform not in selected or len(entry_ids) < 2:
            continue
        conflicts.append(
            {
                "platform": platform,
                "code": code,
                "owners": entry_ids,
                "in_scope": bool(in_scope & set(entry_ids)),
            }
        )

    return {
        "apps": apps,
        "platforms": selected,
        "rows": rows,
        "missing": missing,
        "multi_code": multi_code,
        "format_drift": drift,
        "conflicts": conflicts,
        "conflicts_in_scope": sum(1 for item in conflicts if item["in_scope"]),
        "conflicts_outside_scope": sum(1 for item in conflicts if not item["in_scope"]),
    }


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=root)
    parser.add_argument("--app", action="append", default=None, help="repeatable; default all")
    parser.add_argument("--platform", action="append", default=None, help="repeatable; default all")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    base = args.root.resolve()
    matrix = load_json(base / "data/offer-codes/matrix.json")
    config = load_json(base / "config/marketplace.json")
    platforms = {item["key"]: item for item in config["platforms"]}
    all_platforms = list(platforms)

    apps = args.app or [item["id"] for item in matrix]
    selected = args.platform or all_platforms
    unknown = [platform for platform in selected if platform not in platforms]
    if unknown:
        raise SystemExit(f"unknown platform(s): {', '.join(unknown)}")
    unknown_apps = [app for app in apps if not any(matches(item, app) for item in matrix)]

    report = analyze(matrix, platforms, apps, selected)
    report["unsupported"] = {
        "apps": unknown_apps,
        "platforms": [platform for platform in selected if not platforms[platform].get("urlTemplate")],
    }
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"{len(report['rows'])} code(s), {report['conflicts_in_scope']} conflict(s) in scope")
        for item in report["rows"]:
            print(f"  {item['platform']} {item['app']} {item['code']} {item['url'] or ''}")


if __name__ == "__main__":
    sys.exit(main())
