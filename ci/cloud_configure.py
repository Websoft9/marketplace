#!/usr/bin/env python3
"""Generate a ready-to-load environment file for one configured cloud."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/marketplace.json"
DEFAULT_DIR = ROOT / ".secrets"

EXTRA_TARGETS = ("contentful", "wecom", "websoft9")

# Aliyun China and Alibaba Cloud International are separate accounts. The CLI
# prefers ALIYUN_* for China and keeps ALIBABA_CLOUD_* as a legacy fallback.
ALIYUN_KEYS = """\
ALIYUN_ACCESS_KEY_ID=
ALIYUN_ACCESS_KEY_SECRET=
# Required only for temporary STS credentials:
ALIYUN_SECURITY_TOKEN=
"""

ALIBABA_CLOUD_KEYS = """\
ALIBABA_CLOUD_ACCESS_KEY_ID=
ALIBABA_CLOUD_ACCESS_KEY_SECRET=
# Required only for temporary STS credentials:
ALIBABA_CLOUD_SECURITY_TOKEN=
"""

TEMPLATES = {
    "aws": """\
# --- AWS Marketplace (read-only seller access) ---
# Option A (recommended): configure an SSO/profile outside this file.
# AWS_PROFILE=marketplace-readonly
# Option B: fill the access-key secrets below.
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_SESSION_TOKEN=
AWS_DEFAULT_REGION=us-east-1
""",
    "aliyun": (
        "# --- Aliyun China (least-privilege RAM identity) ---\n"
        + ALIYUN_KEYS
    ),
    "alibabacloud": (
        "# --- Alibaba Cloud International (separate account from Aliyun China) ---\n"
        "# Use this site's own AccessKey; do not reuse the Aliyun China key.\n"
        "# Used by the authenticated Market inventory and public store verification.\n"
        + ALIBABA_CLOUD_KEYS
    ),
    "azure": """\
# --- Azure Marketplace Partner Center ---
# Backend listing adapter is not implemented yet.
AZURE_TENANT_ID=
AZURE_CLIENT_ID=
AZURE_CLIENT_SECRET=
""",
    "azure-vmlab": """\
# --- Azure Marketplace Partner Center (VMLab seller) ---
# Backend listing adapter is not implemented yet.
AZURE_TENANT_ID=
AZURE_CLIENT_ID=
AZURE_CLIENT_SECRET=
""",
    "googlecloud": """\
# --- Google Cloud ---
# Prefer Application Default Credentials; never commit a service-account key.
GOOGLE_CLOUD_PROJECT=
GOOGLE_APPLICATION_CREDENTIALS=
# The current repository integration does not use these credentials.
""",
    "huaweicloud": """\
# --- Huawei Cloud International ---
# Backend listing adapter is not implemented yet.
HUAWEICLOUD_ACCESS_KEY_ID=
HUAWEICLOUD_ACCESS_KEY_SECRET=
""",
    "huaweiyun": """\
# --- Huawei Cloud China ---
# Backend listing adapter is not implemented yet.
HUAWEICLOUD_ACCESS_KEY_ID=
HUAWEICLOUD_ACCESS_KEY_SECRET=
""",
    "contentful": """\
# --- Contentful (catalog pull/push) ---
CONTENTFUL_SPACE_ID=
CONTENTFUL_TOKEN=
CONTENTFUL_ENVIRONMENT=master
# Optional API endpoints (defaults shown):
# CONTENTFUL_API_URL=https://cdn.contentful.com
# CONTENTFUL_MANAGEMENT_API_URL=https://api.contentful.com
""",
    "wecom": """\
# --- WeCom (企业微信) notifications ---
# Incoming webhook URL; treat it as a secret.
WECOM_WEBHOOK=
""",
    "websoft9": """\
# --- Websoft9 account ---
# Base URL of the Websoft9 site or console used by the integration.
WEBSOFT9_URL=http://t.inner.websoft9.cn:9000/
# Use the account credentials required by the target Websoft9 service.
WEBSOFT9_USER=
WEBSOFT9_PASSWORD=
""",
}


def configured_clouds(config_path: Path = CONFIG) -> list[str]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    platforms = config.get("platforms")
    if not isinstance(platforms, list):
        raise ValueError(f"{config_path} has no platforms array")
    keys = [item.get("key") for item in platforms if isinstance(item, dict)]
    if any(not isinstance(key, str) or not key for key in keys):
        raise ValueError(f"{config_path} contains a platform without a key")
    return keys


def configured_targets(config_path: Path = CONFIG) -> list[str]:
    """Cloud platforms plus the non-cloud services used by the workflows."""
    return list(dict.fromkeys(configured_clouds(config_path) + list(EXTRA_TARGETS)))


def render(cloud: str, config_path: Path = CONFIG) -> str:
    targets = configured_targets(config_path)
    if cloud not in targets:
        raise ValueError(f"unknown target {cloud!r}; configured targets: {', '.join(targets)}")
    if cloud not in TEMPLATES:
        raise ValueError(f"no credential template is defined for {cloud!r}")
    return (
        f"# Local configuration for {cloud} only. Load it with mk --env-file.\n"
        "# Fill the blank values; do not commit this file (.secrets/ is ignored).\n"
        + TEMPLATES[cloud]
    )


def write_template(path: Path, content: str, *, force: bool = False) -> None:
    """Write an owner-only file; refuse to overwrite unless ``force`` is set."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent == DEFAULT_DIR:
        os.chmod(path.parent, 0o700)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if force:
            os.replace(temporary, path)
        else:
            try:
                # Hard-link is an atomic no-clobber commit on the same filesystem.
                os.link(temporary, path)
            except FileExistsError:
                raise FileExistsError(f"refusing to overwrite existing file: {path}") from None
    finally:
        Path(temporary).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cloud", required=True,
                        help="Platform/service key from config, or all")
    parser.add_argument("--output", type=Path,
                        help="Output path; defaults to .secrets/<cloud>.env")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing files (the old files are discarded).")
    args = parser.parse_args()
    if args.cloud == "all":
        if args.output is not None:
            parser.exit(1, "error: --output cannot be used with --cloud all\n")
        clouds = configured_targets()
    else:
        clouds = [args.cloud]

    written: list[Path] = []
    skipped: list[str] = []
    for cloud in clouds:
        output = args.output or DEFAULT_DIR / f"{cloud}.env"
        try:
            content = render(cloud)
            write_template(output, content, force=args.force)
            written.append(output)
        except FileExistsError as error:
            skipped.append(str(error))
        except (OSError, ValueError, json.JSONDecodeError) as error:
            parser.exit(1, f"error: {cloud}: {error}\n")

    for output in written:
        print(f"Wrote local configuration: {output}")
    for message in skipped:
        print(f"Skipped: {message}")
    if written:
        print("Fill the blank secrets, then load the file with:")
        if args.cloud == "all":
            print("  mk --env-file .secrets/<cloud>.env <command>")
            print("  make cli cloud=<cloud> ARGS=\"<command>\"")
        else:
            print(f"  mk --env-file {written[0]} <command>")
            print(f"  make cli cloud={args.cloud} ARGS=\"<command>\"")
        print("Do not commit these files or send their values in chat.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
