"""Render the separate AWS AMI asset template: logo and promotional media."""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit
from pathlib import Path
from typing import Any

from .offer import DATA, digest, format_template, listing_targets, read_json, validate_promotional_media
from .aws_marketplace import normalize_promotional_media

ASSETS_TEMPLATE = DATA / "templates" / "aws-ami-assets.json"
ASSETS_TEMPLATE_ID = "aws-ami-assets-v2"
MAX_MEDIA_ITEMS = 5
ASSET_FIELDS = {"LogoUrl", "PromotionalMedia"}


def _unsigned_url(value):
    if not isinstance(value, str):
        raise ValueError("asset URL requires unsigned HTTPS text")
    try:
        parts = urlsplit(value)
        valid = (parts.scheme == "https" and parts.hostname and not parts.username
                 and not parts.password and parts.port in (None, 443)
                 and not parts.query and not parts.fragment and not re.search(r"\s", value))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("asset URL requires an HTTPS URL without credentials, queries or fragments")
    return parts


def _resolve_url(value, base_url):
    if not isinstance(value, str):
        raise ValueError("asset URL must be text")
    parts = urlsplit(value)
    if parts.scheme or parts.netloc:
        _unsigned_url(value)
        return value
    decoded = unquote(value)
    if (not base_url or value.startswith(("/", "\\")) or "\\" in decoded
            or any(part in {".", ".."} for part in decoded.split("/"))):
        raise ValueError("relative asset URL requires baseUrl and cannot escape it")
    result = base_url.rstrip("/") + "/" + value
    _unsigned_url(result)
    return result


def validate_assets_template(template: dict[str, Any]) -> None:
    version = template.get("schemaVersion")
    if type(version) is not int or version not in (1, 2) or template.get("templateId") != f"aws-ami-assets-v{version}":
        raise ValueError("unsupported or mismatched asset template")
    namespaces = template.get("namespaces")
    if not isinstance(namespaces, list) or not namespaces or any(
        not isinstance(namespace, str) or not namespace for namespace in namespaces
    ):
        raise ValueError("asset template.namespaces must contain at least one namespace")
    if "catalog" not in namespaces:
        raise ValueError("asset template requires the catalog namespace")
    target = template.get("target") or {}
    if target.get("platform") not in (None, "aws"):
        raise ValueError("asset template target.platform must be aws")
    if target.get("entityType") not in (None, "AmiProduct@1.0"):
        raise ValueError("asset template target.entityType must be AmiProduct@1.0")
    if target.get("changeType") not in (None, "UpdateInformation"):
        raise ValueError("asset template target.changeType must be UpdateInformation")
    assets = template.get("assets")
    if not isinstance(assets, dict) or not assets:
        raise ValueError("asset template.assets must be a non-empty object")
    unknown = set(assets) - ASSET_FIELDS
    if unknown:
        raise ValueError(
            f"asset template.assets has unsupported key(s): {', '.join(sorted(unknown))}"
        )
    logo = assets.get("LogoUrl")
    if version == 2:
        if "baseUrl" in template:
            _unsigned_url(template["baseUrl"])
            if any(c in template["baseUrl"] for c in "{}\\"):
                raise ValueError("baseUrl must be a static unsigned HTTPS base URL")
        if logo is not None and not isinstance(logo, str):
            raise ValueError("asset template.assets.LogoUrl must be text")
        media = assets.get("PromotionalMedia")
        if not isinstance(media, list) or not 1 <= len(media) <= MAX_MEDIA_ITEMS:
            raise ValueError(f"PromotionalMedia must contain 1–{MAX_MEDIA_ITEMS} items")
        for index, item in enumerate(media):
            path = f"PromotionalMedia[{index}]"
            if not isinstance(item, dict):
                raise ValueError(f"{path} must be an object")
            if set(item) - {"Type", "Url", "Title", "Description"}:
                raise ValueError(f"{path} has unsupported fields")
            if item.get("Type") != "Image":
                raise ValueError(f"{path}.Type must be Image for this template")
            for field in ("Url", "Title"):
                if not isinstance(item.get(field), str) or not item[field].strip():
                    raise ValueError(f"{path}.{field} must be non-empty text")
            if "Description" in item and not isinstance(item["Description"], str):
                raise ValueError(f"{path}.Description must be text")
        return
    if logo is not None:
        if not isinstance(logo, dict) or not isinstance(logo.get("source"), str):
            raise ValueError("asset template.assets.LogoUrl.source must be text")
        if set(logo) - {"source", "optional"}:
            raise ValueError("asset template.assets.LogoUrl has unsupported key(s)")
    media = assets.get("PromotionalMedia")
    if not isinstance(media, dict):
        raise ValueError("asset template.assets.PromotionalMedia must be an object")
    unknown = set(media) - {"source", "maxItems", "type", "title", "description"}
    if unknown:
        raise ValueError(
            "asset template.assets.PromotionalMedia has unsupported key(s): "
            + ", ".join(sorted(unknown))
        )
    if not isinstance(media.get("source"), str):
        raise ValueError("PromotionalMedia.source must be text")
    max_items = media.get("maxItems")
    if not isinstance(max_items, int) or isinstance(max_items, bool) or not 1 <= max_items <= MAX_MEDIA_ITEMS:
        raise ValueError(f"PromotionalMedia.maxItems must be 1–{MAX_MEDIA_ITEMS}")
    if media.get("type") != "Image":
        raise ValueError("PromotionalMedia.type must be Image for this template")
    if not isinstance(media.get("title"), str) or not media["title"].strip():
        raise ValueError("PromotionalMedia.title must be non-empty text")
    description = media.get("description")
    if description is not None and not isinstance(description, str):
        raise ValueError("PromotionalMedia.description must be text")


def _asset_url(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("imageurl") or value.get("url")
    if isinstance(value, str) and value.startswith("https://"):
        return value
    return None


def _screenshot_url(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("value") or value.get("url")
    if isinstance(value, str) and value.startswith("https://"):
        return value
    return None


def _is_s3_url(value: str) -> bool:
    host = value.split("/", 3)[2].split(":", 1)[0].lower() if "://" in value else ""
    return (
        host == "s3.amazonaws.com"
        or host.startswith("s3.")
        or host.endswith(".s3.amazonaws.com")
        or ".s3." in host
    )


def _validate_text(value: str, path: str) -> None:
    if re.search(r"[\x00-\x08\x0b-\x1f]", value):
        raise ValueError(f"{path} contains unsupported control characters: {value[:120]!r}")
    if "  " in value:
        raise ValueError(f"{path} contains consecutive spaces: {value[:120]!r}")
    if re.search(r"\{[A-Za-z][A-Za-z0-9_]*\}", value):
        raise ValueError(f"{path} contains unresolved template placeholders: {value[:120]!r}")


def validate_assets_content(content: dict[str, Any]) -> None:
    """Validate only the asset fields, not the required copy fields."""
    unknown = set(content) - ASSET_FIELDS
    if unknown:
        raise ValueError(
            "unsupported asset field(s): " + ", ".join(sorted(unknown))
        )
    logo = content.get("LogoUrl")
    if logo is not None:
        if not isinstance(logo, str) or not re.fullmatch(r"https://[^\s?#]+", logo):
            raise ValueError("LogoUrl requires an unsigned HTTPS URL")
        _validate_text(logo, "LogoUrl")
    if "PromotionalMedia" in content:
        media = content["PromotionalMedia"]
        validate_promotional_media(media)
        for index, item in enumerate(media):
            _validate_text(item["Url"], f"PromotionalMedia[{index}].Url")
            _validate_text(item["Title"], f"PromotionalMedia[{index}].Title")
            if item.get("Description"):
                _validate_text(
                    item["Description"], f"PromotionalMedia[{index}].Description"
                )


def _render_catalog_assets(
    catalog_item: dict[str, Any], template: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """Render the legacy v1 catalog-sourced assets."""
    roots = {"catalog": catalog_item}
    content: dict[str, Any] = {}
    warnings: list[str] = []

    logo_spec = template["assets"].get("LogoUrl") or {}
    logo_source = logo_spec.get("source")
    if logo_source:
        try:
            logo_value = format_template(logo_source, roots)
        except ValueError:
            logo_value = None
        logo_url = _asset_url(logo_value)
        if logo_url:
            content["LogoUrl"] = logo_url
        else:
            warnings.append("no logo image URL in the catalog item")
    if "LogoUrl" not in content and not logo_spec.get("optional"):
        warnings.append("LogoUrl is required by AWS but was not rendered")

    media_spec = template["assets"]["PromotionalMedia"]
    try:
        source = format_template(media_spec["source"], roots)
    except ValueError:
        source = []
    if source is None:
        source = []
    if not isinstance(source, list):
        raise ValueError("PromotionalMedia.source must resolve to an array")
    max_items = media_spec["maxItems"]
    media: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for index, raw in enumerate(source, 1):
        if len(media) >= max_items:
            break
        url = _screenshot_url(raw)
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        item_roots = {
            **roots,
            "index": index,
            "key": raw.get("key", "") if isinstance(raw, dict) else "",
        }
        item: dict[str, Any] = {
            "Type": media_spec["type"],
            "Url": url,
            "Title": format_template(media_spec["title"], item_roots),
        }
        description_template = media_spec.get("description")
        if description_template:
            description = format_template(description_template, item_roots)
            if description.strip():
                item["Description"] = description
        media.append(item)
    if media:
        content["PromotionalMedia"] = media
    else:
        warnings.append("no screenshots in the catalog item; PromotionalMedia was not rendered")

    if len(source) > max_items:
        warnings.append(
            f"catalog has {len(source)} screenshots; only the first {max_items} were rendered"
        )
    return content, warnings


def render_assets(
    catalog_item: dict[str, Any],
    template_path: Path = ASSETS_TEMPLATE,
) -> dict[str, Any]:
    """Build a separate asset preview from v2 entries or legacy v1 sources."""
    template = read_json(template_path)
    validate_assets_template(template)
    if template["schemaVersion"] == 1:
        content, warnings = _render_catalog_assets(catalog_item, template)
    else:
        roots = {"catalog": catalog_item}
        assets = template["assets"]
        content = {}
        warnings = []
        if assets.get("LogoUrl") is not None:
            content["LogoUrl"] = _resolve_url(format_template(assets["LogoUrl"], roots), template.get("baseUrl"))
        else:
            warnings.append("LogoUrl is required by AWS but was not rendered")
        content["PromotionalMedia"] = [
            {field: format_template(value, roots) for field, value in item.items()}
            for item in assets["PromotionalMedia"]
        ]
        for item in content["PromotionalMedia"]:
            item["Url"] = _resolve_url(item["Url"], template.get("baseUrl"))
    if content:
        validate_assets_content(content)
    non_s3 = [url for url in ([content.get("LogoUrl")] + [
        item["Url"] for item in content.get("PromotionalMedia", [])
    ]) if url and not _is_s3_url(url)]
    if non_s3:
        warnings.append(
            f"{len(non_s3)} asset URL(s) are not S3-hosted; upload to the intended public asset location before apply"
        )
    return {
        "schemaVersion": 1,
        "key": catalog_item.get("id"),
        "entityId": catalog_item.get("entityId"),
        "entityType": catalog_item.get("entityType") or "AmiProduct@1.0",
        "templateId": template["templateId"],
        "content": content,
        "preview": {
            "templateId": template["templateId"],
            "target": template.get("target", {}),
            "content": content,
            "assetSummary": {
                "logo": bool(content.get("LogoUrl")),
                "media": len(content.get("PromotionalMedia") or []),
            },
        },
        "warnings": warnings,
        "sourceHash": digest({"catalog": catalog_item, "template": template}),
    }


def as_offer_bundle(item, document):
    """Use the authored asset JSON directly; no image reads, staging or byte hashes."""
    if (not isinstance(document, dict) or type(document.get("schemaVersion")) is not int
            or document["schemaVersion"] != 1 or document.get("kind") == "staged-offer-assets"
            or "manifestHash" in document or "files" in document):
        raise ValueError("--assets requires offer assets build JSON; legacy staged manifests must be rebuilt")
    if not isinstance(document.get("key"), str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,99}", document["key"]):
        raise ValueError("asset document requires a valid application key")
    if (item.get("id") != document.get("key")
            or (item.get("entityType") or "AmiProduct@1.0") != "AmiProduct@1.0"):
        raise ValueError("asset document does not belong to this application/AWS AMI type")
    content = document.get("content")
    if not isinstance(content, dict) or not content or set(content) - ASSET_FIELDS:
        raise ValueError("asset document must contain only non-empty LogoUrl/PromotionalMedia content")
    media = content.get("PromotionalMedia", [])
    if not isinstance(media, list) or any(not isinstance(p, dict) or "Url" not in p for p in media):
        raise ValueError("PromotionalMedia must be a list of image records")
    for url in ([content["LogoUrl"]] if "LogoUrl" in content else []) + [p["Url"] for p in media]:
        _unsigned_url(url)
    validate_assets_content(content)
    if len(media) > MAX_MEDIA_ITEMS or any(p["Type"] != "Image" for p in media):
        raise ValueError("asset publishing supports at most five images and no videos")
    desired = dict(content)
    if "PromotionalMedia" in desired:
        desired["PromotionalMedia"] = normalize_promotional_media(media)
    targets = listing_targets(item)
    return {"schemaVersion": 1, "key": item["id"], "entityId": item.get("entityId"),
            "entityType": "AmiProduct@1.0", "content": desired, "targets": targets,
            "fieldScope": "assets", "publishBlockers": [] if targets else ["listing: Catalog EntityId is missing"],
            "sourceHash": digest({"fieldScope": "assets", "assets": document, "catalog": item})}
