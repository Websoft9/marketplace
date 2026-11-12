#!/usr/bin/env python3
"""Fetch marketplace product pages once and extract fields used by status/content checks."""

from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import sys
import urllib.error
import urllib.request


USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
DESCRIPTION = re.compile(
    r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']*)', re.I
)
OG_IMAGE = re.compile(
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']*)', re.I
)
IMAGE = re.compile(r"<img\b[^>]*\bsrc=[\"']([^\"']+)", re.I)
VERSION_KEY = re.compile(
    r"(imageVersion|RecommendedVersion|version|Version)\s*[\"']?\s*[:=]\s*[\"']([^\"']{1,60})"
)
VERSION_TEXT = re.compile(r"(?:版本|Version|version)[^<\n]{0,40}")
BLOCKED = re.compile(r"验证码|captcha|请登录|sign in to continue|access denied", re.I)


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(value)).strip()


def extract(html: str) -> dict:
    """Extract check fields from a page. No network access."""
    title = TITLE.search(html)
    description = DESCRIPTION.search(html)
    og_image = OG_IMAGE.search(html)
    samples: list[str] = []
    decoded = html_lib.unescape(html)
    for match in VERSION_KEY.finditer(decoded):
        value = f"{match.group(1)}: {clean(match.group(2))}"
        if value not in samples:
            samples.append(value)
        if len(samples) >= 5:
            break
    if not samples:
        for match in VERSION_TEXT.finditer(decoded):
            value = clean(match.group(0))
            if len(value) > 6 and value not in samples:
                samples.append(value)
            if len(samples) >= 5:
                break
    return {
        "title": clean(title.group(1)) if title else "",
        "description": clean(description.group(1)) if description else "",
        "og_image": og_image.group(1).strip() if og_image else "",
        "image_count": len(IMAGE.findall(html)),
        "version_samples": samples,
        "blocked_hint": bool(BLOCKED.search(html)),
    }


def fetch(url: str, timeout: float = 30) -> dict:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en,*;q=0.5"}
    )
    result: dict = {"url": url, "status": None, "final_url": url, "error": ""}
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            result["status"] = response.status
            result["final_url"] = response.url
            charset = response.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as error:
        result["status"] = error.code
        result["error"] = f"HTTP {error.code}"
        charset = "utf-8"
        body = b""
    except Exception as error:  # noqa: BLE001 - report any fetch failure as unverified
        result["error"] = type(error).__name__
        charset = "utf-8"
        body = b""
    result.update(extract(body.decode(charset, errors="replace")))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", action="append", required=True, help="product URL, repeatable")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    results = [fetch(url, args.timeout) for url in args.url]
    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        for item in results:
            print(f"{item['url']} -> {item['status']} {item['final_url']}")
            print(f"  title: {item['title'] or '(none)'}")
            print(f"  description: {'yes' if item['description'] else 'no'}")
            print(f"  images: {item['image_count']}")
            print(f"  version samples: {item['version_samples']}")
            if item["error"]:
                print(f"  error: {item['error']}")


if __name__ == "__main__":
    sys.exit(main())
