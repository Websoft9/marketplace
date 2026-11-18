"""Resolve Aliyun China credentials from the ``ALIYUN_*`` environment variables.

Aliyun China and Alibaba Cloud International are separate accounts. The
China-specific ``ALIYUN_*`` names are the only supported form here; the
international marketplace uses its own ``ALIBABA_CLOUD_*`` configuration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Credentials:
    access_key_id: str
    access_key_secret: str
    security_token: str = ""


def read() -> Credentials:
    return Credentials(
        access_key_id=os.environ.get("ALIYUN_ACCESS_KEY_ID", "").strip(),
        access_key_secret=os.environ.get("ALIYUN_ACCESS_KEY_SECRET", "").strip(),
        security_token=os.environ.get("ALIYUN_SECURITY_TOKEN", "").strip(),
    )


def missing_hint() -> str:
    return (
        "ALIYUN_ACCESS_KEY_ID and ALIYUN_ACCESS_KEY_SECRET "
        "(and ALIYUN_SECURITY_TOKEN for temporary STS credentials)"
    )
