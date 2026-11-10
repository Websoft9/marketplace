"""Backend listing checks for marketplace offer codes.

Each cloud is a small adapter. The command reads credentials from the
environment and asks the seller API whether the code still resolves and how
visible it is; it never opens public product pages. A platform without an
adapter or credentials is reported as ``unsupported`` or ``skipped`` instead of
being guessed.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from . import offer_codes

EXISTS = "exists"
NOT_FOUND = "not_found"
UNSUPPORTED = "unsupported"
SKIPPED = "skipped"
ERROR = "error"

ALIBABA_CLOUD_DETAIL_API = (
    "https://marketplace.alibabacloud.com/api/ajax/newCommodityDetail/"
    "commodityInfo.json?"
)
ALIBABA_CLOUD_REFERER = (
    "https://marketplace.alibabacloud.com/products/201072001/{code}.html"
)


@dataclass
class Outcome:
    status: str
    detail: str = ""
    visibility: str = ""
    public: bool | None = None


class Adapter:
    platform = ""
    required_env: tuple[str, ...] = ()
    supported = True

    def availability(self) -> tuple[bool, str]:
        if not self.supported:
            return False, "no backend adapter yet"
        missing = [name for name in self.required_env if not os.environ.get(name)]
        if missing:
            return False, "missing environment variable(s): " + ", ".join(missing)
        return True, ""

    def check(self, codes: list[str]) -> dict[str, Outcome]:  # pragma: no cover
        raise NotImplementedError


class AwsAdapter(Adapter):
    platform = "aws"
    required_env = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")

    def check(self, codes: list[str]) -> dict[str, Outcome]:
        # Reuse the seller Catalog resolver that mk offer already ships.
        from .commands.offer import _resolve_aws_entities

        profile = os.environ.get("AWS_PROFILE") or None
        matches = _resolve_aws_entities(tuple(codes), profile)
        results: dict[str, Outcome] = {}
        for code in codes:
            found = matches.get(code) or []
            if not found:
                results[code] = Outcome(NOT_FOUND, "no seller Catalog entity matches this code")
            elif len(found) > 1:
                results[code] = Outcome(
                    ERROR, f"ambiguous: {len(found)} seller entities match this code"
                )
            else:
                visibility = str(found[0].get("visibility") or "")
                detail = f"entity {found[0].get('entityId')}"
                public = visibility.lower() == "public" if visibility else None
                results[code] = Outcome(
                    EXISTS, detail, visibility=visibility, public=public
                )
        return results


class AliyunAdapter(Adapter):
    platform = "aliyun"
    required_env = ("ALIBABA_CLOUD_ACCESS_KEY_ID", "ALIBABA_CLOUD_ACCESS_KEY_SECRET")

    def availability(self) -> tuple[bool, str]:
        available, reason = super().availability()
        if not available:
            return available, reason
        try:
            import aliyunsdkcore.client  # noqa: F401
            import aliyunsdkmarket  # noqa: F401
        except ImportError:
            return False, "install the Aliyun SDK with: pip install '.[aliyun]'"
        return True, ""

    def check(self, codes: list[str]) -> dict[str, Outcome]:
        from aliyunsdkcore.auth.credentials import StsTokenCredential
        from aliyunsdkcore.client import AcsClient
        from aliyunsdkmarket.request.v20151101.DescribeProductRequest import (
            DescribeProductRequest,
        )

        access_key = os.environ["ALIBABA_CLOUD_ACCESS_KEY_ID"].strip()
        access_secret = os.environ["ALIBABA_CLOUD_ACCESS_KEY_SECRET"].strip()
        security_token = os.environ.get("ALIBABA_CLOUD_SECURITY_TOKEN", "").strip()
        if security_token:
            credentials = StsTokenCredential(
                sts_access_key_id=access_key,
                sts_access_key_secret=access_secret,
                sts_token=security_token,
            )
            client = AcsClient(region_id="cn-hangzhou", credential=credentials)
        else:
            client = AcsClient(access_key, access_secret, "cn-hangzhou")

        results: dict[str, Outcome] = {}
        for code in codes:
            request = DescribeProductRequest()
            request.set_Code(code)
            request.set_QueryDraft(False)
            try:
                response = client.do_action_with_exception(request)
                parsed = json.loads(
                    response.decode("utf-8") if isinstance(response, bytes) else response
                )
            except Exception as error:  # SDK errors may contain signed details
                error_code = ""
                getter = getattr(error, "get_error_code", None)
                if callable(getter):
                    error_code = str(getter() or "")
                if error_code in {"ProductNotExist", "InvalidProductCode", "ProductNotFound"}:
                    results[code] = Outcome(NOT_FOUND, f"Aliyun error {error_code}")
                else:
                    results[code] = Outcome(
                        ERROR, f"Aliyun API failed ({error_code or type(error).__name__})"
                    )
                continue
            payload = parsed.get("Result", parsed) if isinstance(parsed, dict) else None
            if isinstance(parsed, dict) and parsed.get("Success") is False:
                message = str(parsed.get("Code") or "Aliyun returned Success=false")
                status = NOT_FOUND if "not" in message.lower() else ERROR
                results[code] = Outcome(status, message)
            elif isinstance(payload, dict) and payload:
                results[code] = Outcome(EXISTS, str(payload.get("Name") or ""))
            else:
                results[code] = Outcome(NOT_FOUND, "Aliyun returned no product")
        return results


class AlibabaCloudAdapter(Adapter):
    platform = "alibabacloud"
    required_env = ()

    def check(self, codes: list[str]) -> dict[str, Outcome]:
        results: dict[str, Outcome] = {}
        for code in codes:
            query = urllib.parse.urlencode({"commodityCode": code})
            request = urllib.request.Request(
                f"{ALIBABA_CLOUD_DETAIL_API}{query}",
                headers={
                    "User-Agent": "Websoft9-manufacture/1.0",
                    "Accept": "application/json, text/plain, */*",
                    "Referer": ALIBABA_CLOUD_REFERER.format(
                        code=urllib.parse.quote(code, safe="")
                    ),
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    payload = json.loads(response.read())
            except urllib.error.HTTPError as error:
                if error.code in {404, 410}:
                    results[code] = Outcome(NOT_FOUND, f"HTTP {error.code}")
                else:
                    results[code] = Outcome(ERROR, f"HTTP {error.code}")
                continue
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
                results[code] = Outcome(
                    ERROR, f"Alibaba Cloud API failed ({type(error).__name__})"
                )
                continue
            if not isinstance(payload, dict):
                results[code] = Outcome(ERROR, "Alibaba Cloud returned a non-object response")
            elif payload.get("success") is True:
                commodity = (
                    (payload.get("result") or {}).get("product") or {}
                ).get("commodityInfo") or {}
                title = ""
                if isinstance(commodity, dict):
                    title = str(commodity.get("name") or commodity.get("title") or "")
                results[code] = Outcome(EXISTS, title)
            else:
                results[code] = Outcome(NOT_FOUND, "Alibaba Cloud returned success=false")
        return results


class UnsupportedAdapter(Adapter):
    supported = False

    def __init__(self, platform: str, reason: str, required_env: tuple[str, ...] = ()) -> None:
        self.platform = platform
        self.reason = reason
        self.required_env = required_env

    def availability(self) -> tuple[bool, str]:
        return False, self.reason


ADAPTERS: dict[str, Adapter] = {
    "aws": AwsAdapter(),
    "aliyun": AliyunAdapter(),
    "alibabacloud": AlibabaCloudAdapter(),
    "azure": UnsupportedAdapter(
        "azure",
        "Azure Partner Center adapter is not implemented; expected "
        "AZURE_TENANT_ID, AZURE_CLIENT_ID and AZURE_CLIENT_SECRET",
        ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"),
    ),
    "huaweicloud": UnsupportedAdapter(
        "huaweicloud",
        "Huawei Cloud seller adapter is not implemented; expected "
        "HUAWEICLOUD_ACCESS_KEY_ID and HUAWEICLOUD_ACCESS_KEY_SECRET",
        ("HUAWEICLOUD_ACCESS_KEY_ID", "HUAWEICLOUD_ACCESS_KEY_SECRET"),
    ),
    "huaweiyun": UnsupportedAdapter(
        "huaweiyun",
        "Huawei Cloud seller adapter is not implemented; expected "
        "HUAWEICLOUD_ACCESS_KEY_ID and HUAWEICLOUD_ACCESS_KEY_SECRET",
        ("HUAWEICLOUD_ACCESS_KEY_ID", "HUAWEICLOUD_ACCESS_KEY_SECRET"),
    ),
}


def _result(
    platform: str,
    item: dict[str, Any],
    outcome: Outcome,
) -> dict[str, Any]:
    return {
        "platform": platform,
        "code": item["code"],
        "apps": item.get("apps") or [],
        "status": outcome.status,
        "detail": outcome.detail,
        "visibility": outcome.visibility,
        "public": outcome.public,
    }


def check_listings(
    documents: list[tuple[str, Any]],
    *,
    platforms: list[str] | None = None,
    limit: int = 0,
    adapters: dict[str, Adapter] | None = None,
) -> dict[str, Any]:
    """Check listing existence and visibility through each backend adapter."""
    registry = adapters if adapters is not None else ADAPTERS
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in offer_codes.collect_codes(documents):
        grouped.setdefault(item["platform"], []).append(item)

    selected = platforms or sorted(grouped)
    unknown = [name for name in selected if name not in registry]
    if unknown:
        raise ValueError("unknown platform(s): " + ", ".join(unknown))

    results: list[dict[str, Any]] = []
    reports: dict[str, dict[str, Any]] = {}
    for platform in selected:
        items = grouped.get(platform, [])
        adapter = registry.get(platform)
        if not items:
            reports[platform] = {"status": "empty", "checked": 0}
            continue
        targets = items[:limit] if limit and limit > 0 else items
        if adapter is None:
            reason = "no adapter registered for this platform"
            reports[platform] = {"status": UNSUPPORTED, "checked": 0, "reason": reason}
            results.extend(
                _result(platform, item, Outcome(UNSUPPORTED, reason)) for item in targets
            )
            continue
        available, reason = adapter.availability()
        if not available:
            status = UNSUPPORTED if not adapter.supported else SKIPPED
            reports[platform] = {"status": status, "checked": 0, "reason": reason}
            results.extend(
                _result(platform, item, Outcome(status, reason)) for item in targets
            )
            continue
        try:
            outcome = adapter.check([item["code"] for item in targets])
        except Exception as error:  # keep the run going for other platforms
            reason = f"adapter failed ({type(error).__name__})"
            reports[platform] = {"status": ERROR, "checked": 0, "reason": reason}
            results.extend(
                _result(platform, item, Outcome(ERROR, reason)) for item in targets
            )
            continue
        counts: dict[str, int] = {}
        not_public = 0
        for item in targets:
            value = outcome.get(item["code"]) or Outcome(ERROR, "no result returned")
            counts[value.status] = counts.get(value.status, 0) + 1
            if value.status == EXISTS and value.public is False:
                not_public += 1
            results.append(_result(platform, item, value))
        reports[platform] = {
            "status": "ok",
            "checked": len(targets),
            "not_public": not_public,
            **counts,
        }

    summary = {
        "checked": len(results),
        "exists": sum(1 for item in results if item["status"] == EXISTS),
        "not_found": sum(1 for item in results if item["status"] == NOT_FOUND),
        "skipped": sum(1 for item in results if item["status"] == SKIPPED),
        "unsupported": sum(1 for item in results if item["status"] == UNSUPPORTED),
        "errors": sum(1 for item in results if item["status"] == ERROR),
        "not_public": sum(
            1 for item in results
            if item["status"] == EXISTS and item.get("public") is False
        ),
    }
    return {"summary": summary, "platforms": reports, "results": results}
