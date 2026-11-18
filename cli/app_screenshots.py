"""Sequential application screenshot production; browser and S3 imports stay optional."""

from __future__ import annotations

import json
import os
import re
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from .catalog_data import load_catalog

BUCKET = "assets-797851739507-us-east-1-an"
REGION = "us-east-1"
OWNER = "797851739507"
VIEWPORT = {"width": 1920, "height": 1040}
PUBLIC_BASE = f"https://{BUCKET}.s3.{REGION}.amazonaws.com"
APP_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,99}\Z", re.ASCII)
ERROR_DESCRIPTIONS = {
    "no_search_results": "AppStore explicitly reported no matching applications.",
    "multiple_matching_results": "More than one application matched; none was selected.",
    "trademark_not_matched": "Search returned applications, but none exactly matched the catalog trademark.",
    "search_loading_timeout": "AppStore was still loading when the search wait expired.",
    "search_results_timeout": "No usable search results or explicit empty-results state appeared in time.",
}


class ScreenshotError(ValueError):
    """Safe user-facing error; never include underlying browser or SDK messages."""


@dataclass(frozen=True)
class Credentials:
    url: str = field(repr=False)
    username: str = field(repr=False)
    password: str = field(repr=False)

    @classmethod
    def from_environment(cls) -> Credentials:
        names = ("WEBSOFT9_URL", "WEBSOFT9_USER", "WEBSOFT9_PASSWORD")
        if any(not os.environ.get(name, "").strip() for name in names):
            raise ScreenshotError("Set WEBSOFT9_URL, WEBSOFT9_USER and WEBSOFT9_PASSWORD.")
        url, username, password = (os.environ[name] for name in names)
        try:
            parsed = urlsplit(url)
            valid = (parsed.scheme in ("http", "https") and parsed.hostname and parsed.port != 0
                     and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment)
        except ValueError:
            valid = False
        if not valid:
            raise ScreenshotError("WEBSOFT9_URL must be an HTTP(S) URL without embedded credentials, query or fragment.")
        return cls(url, username, password)


def select_applications(source: str, selection: str | None = None) -> list[dict[str, str]]:
    try:
        _, catalog = load_catalog(source)
    except (OSError, ValueError):
        raise ScreenshotError("Unable to read a valid application catalog.") from None
    if not catalog or any(not APP_ID.fullmatch(key) for key in catalog):
        raise ScreenshotError("Catalog must contain applications with safe lowercase IDs.")
    ids = list(catalog)
    if selection is not None:
        requested = selection.split(",")
        if any(not APP_ID.fullmatch(key.strip()) for key in requested):
            raise ScreenshotError("--apps must contain comma-separated lowercase application IDs.")
        ids = list(dict.fromkeys(key.strip() for key in requested))
        if any(key not in catalog for key in ids):
            raise ScreenshotError("One or more requested application IDs are not in the catalog.")
    applications = []
    for key in ids:
        trademark = catalog[key].get("trademark")
        # A display name is optional, not an identity requirement. The browser
        # verifies the actual app ID in the detail view before taking a screenshot.
        applications.append({"id": key, "trademark": trademark.strip() if isinstance(trademark, str) else ""})
    return applications


def validate_png(image: bytes) -> None:
    if (len(image) < 33 or image[:8] != b"\x89PNG\r\n\x1a\n" or image[12:16] != b"IHDR"
            or struct.unpack(">II", image[16:24]) != (VIEWPORT["width"], VIEWPORT["height"])):
        raise ScreenshotError("invalid_screenshot")


def create_s3_client(profile: str | None = None):
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        raise ScreenshotError("Install screenshot dependencies with pip install '.[screenshots]'.") from None
    try:
        return boto3.Session(profile_name=profile).client(
            "s3", region_name=REGION,
            config=Config(connect_timeout=10, read_timeout=30, retries={"mode": "standard", "max_attempts": 3}),
        )
    except Exception:
        raise ScreenshotError("Unable to initialize S3 credentials/client.") from None


def upload_screenshot(client, appid: str, image: bytes) -> str:
    key = f"offers/{appid}-installation.png"
    try:
        client.put_object(
            Bucket=BUCKET, Key=key, Body=image, ContentType="image/png",
            CacheControl="public, max-age=300", ExpectedBucketOwner=OWNER,
        )
    except Exception as error:
        # Export only fixed classifications, never SDK messages or response bodies.
        response = getattr(error, "response", {})
        code = response.get("Error", {}).get("Code") if isinstance(response, dict) else None
        failure = {
            "AccessDenied": "upload_access_denied",
            "AllAccessDisabled": "upload_access_denied",
            "ExpiredToken": "upload_credentials_expired",
            "ExpiredTokenException": "upload_credentials_expired",
            "InvalidAccessKeyId": "upload_invalid_credentials",
            "SignatureDoesNotMatch": "upload_invalid_credentials",
            "NoSuchBucket": "upload_bucket_not_found",
        }.get(code, "upload_failed")
        raise ScreenshotError(failure) from None
    return f"{PUBLIC_BASE}/{key}"


def run_screenshots(
    applications: list[dict[str, str]], *, credentials: Credentials, output_dir: Path,
    upload: bool = True, profile: str | None = None, timeout: int = 30,
    progress: Callable[[str], None] = lambda message: None,
) -> dict:
    from .appstore_browser import AppStoreBrowser

    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    report = {
        "startedAt": datetime.now(timezone.utc).isoformat(),
        "viewport": dict(VIEWPORT), "upload": upload, "results": [],
        "succeeded": 0, "failed": 0,
    }

    def save_report():
        report["succeeded"] = sum(item["status"] == "success" for item in report["results"])
        report["failed"] = sum(item["status"] == "failed" for item in report["results"])
        (output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    # Clear a previous run's report before starting. Never export raw exceptions.
    save_report()
    try:
        client = create_s3_client(profile) if upload else None
        with AppStoreBrowser(credentials, timeout=timeout) as browser:
            for index, app in enumerate(applications, 1):
                appid = app["id"]
                result = {"appid": appid, "status": "failed"}
                phase = "screenshot_failed"
                try:
                    image = browser.capture(app)
                    validate_png(image)
                    filename = f"{appid}-installation.png"
                    phase = "local_write_failed"
                    (output_dir / filename).write_bytes(image)
                    result["file"] = filename
                    if upload:
                        phase = "upload_failed"
                        result["url"] = upload_screenshot(client, appid, image)
                    result["status"] = "success"
                except ScreenshotError as error:
                    result["error"] = str(error)
                except Exception:
                    result["error"] = phase
                report["results"].append(result)
                save_report()
                progress(f"[{index}/{len(applications)}] {appid}: {result['status']}"
                         + (f" ({result['error']})" if "error" in result else ""))
    except Exception as error:
        # Fatal setup/login failure: mark remaining items and preserve earlier results.
        code = str(error) if isinstance(error, ScreenshotError) else "browser_setup_failed"
        report["error"] = code
        done = {item["appid"] for item in report["results"]}
        report["results"].extend(
            {"appid": app["id"], "status": "failed", "error": code}
            for app in applications if app["id"] not in done
        )
        progress(f"Screenshot run failed: {code}")
    report["finishedAt"] = datetime.now(timezone.utc).isoformat()
    save_report()
    return report


def render_summary(report: dict) -> str:
    failed = [item["appid"] for item in report["results"] if item["status"] == "failed"]
    lines = ["## AppStore screenshots", "",
             f"{report['succeeded']} succeeded, {report['failed']} failed. Viewport: 1920 × 1040.",
             "", f"Failed list: {', '.join(failed) if failed else 'none'}",
             "", "| Application | Status | Result |", "| --- | --- | --- |"]
    for item in report["results"]:
        detail = item.get("error") or item.get("url") or "Local only"
        if item.get("error") in ERROR_DESCRIPTIONS:
            detail += ": " + ERROR_DESCRIPTIONS[item["error"]]
        lines.append(f"| {item['appid']} | {item['status']} | {detail} |")
    return "\n".join(lines) + "\n"
