#!/usr/bin/env python3
"""Compare a marketplace application version with the docker-library baseline."""

from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.request
from pathlib import Path


TOKEN = re.compile(r"^v?(\d+(?:\.\d+)*)(?:[-+]([0-9A-Za-z.\-]+))?$")
CONFIG = Path(__file__).resolve().parents[2] / "config/app.json"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)


def parse_version(token: str) -> tuple[tuple[int, ...], bool] | None:
    """Return (trimmed numeric tuple, is_prerelease) or None for unsupported schemes."""
    match = TOKEN.match(token.strip())
    if not match:
        return None
    numbers = [int(part) for part in match.group(1).split(".")]
    while len(numbers) > 1 and numbers[-1] == 0:
        numbers.pop()
    suffix = match.group(2) or ""
    prerelease = bool(re.search(r"(alpha|beta|rc|pre|dev)", suffix, re.I))
    return tuple(numbers), prerelease


def sort_key(parsed: tuple[tuple[int, ...], bool]) -> tuple[tuple[int, ...], int]:
    numbers, prerelease = parsed
    return numbers, 0 if prerelease else 1


def format_version(numbers: tuple[int, ...]) -> str:
    return ".".join(str(part) for part in numbers)


def compare(marketplace: list[str], baseline: list[str]) -> dict:
    """Compare extracted marketplace versions with baseline versions."""
    marketplace_parsed = [parse_version(item) for item in marketplace]
    baseline_parsed = [parse_version(item) for item in baseline if item != "latest"]

    if not marketplace:
        return {"status": "unverified", "reason": "no marketplace version found"}
    if any(item is None for item in marketplace_parsed):
        return {"status": "unverified", "reason": "marketplace version scheme is not comparable"}
    if not baseline_parsed:
        return {"status": "unsupported", "reason": "baseline has no comparable version"}
    if any(item is None for item in baseline_parsed):
        return {"status": "unsupported", "reason": "baseline version scheme is not comparable"}

    newest_marketplace = max(marketplace_parsed, key=sort_key)
    newest_baseline = max(baseline_parsed, key=sort_key)
    result = {
        "marketplace": marketplace,
        "baseline": baseline,
        "marketplace_newest": format_version(newest_marketplace[0]),
        "baseline_newest": format_version(newest_baseline[0]),
    }
    if sort_key(newest_marketplace) < sort_key(newest_baseline):
        return result | {"status": "outdated", "reason": "marketplace newest version is lower"}
    return result | {"status": "current", "reason": "marketplace newest version is not lower"}


def load_baseline(app: str, source: str) -> list[str]:
    if source.startswith("http"):
        request = urllib.request.Request(source, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise SystemExit(f"cannot read version baseline: HTTP {error.code}") from error
    else:
        try:
            data = json.loads(Path(source).read_text(encoding="utf-8"))
        except OSError as error:
            raise SystemExit(f"cannot read version baseline: {error}") from error
    for entry in data:
        if entry.get("id") == app:
            return list(entry.get("version", []))
    raise SystemExit(f"application {app!r} not found in baseline")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True)
    parser.add_argument("--marketplace", required=True, help="comma-separated versions from the listing")
    parser.add_argument("--baseline-source", default=None, help="URL or file; defaults to config/app.json")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    source = args.baseline_source
    if not source:
        source = json.loads(CONFIG.read_text(encoding="utf-8"))["sources"]["versions"]
    marketplace = [item.strip() for item in args.marketplace.split(",") if item.strip()]
    result = compare(marketplace, load_baseline(args.app, source))
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"{args.app}: {result['status']} — {result.get('reason', '')}")


if __name__ == "__main__":
    main()
