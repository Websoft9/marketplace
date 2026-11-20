"""Resolve Aliyun China and Alibaba Cloud International credentials.

Aliyun China and Alibaba Cloud International are separate accounts. The
China-specific ``ALIYUN_*`` names are the only supported form for China; the
international marketplace uses its own ``ALIBABA_CLOUD_*`` configuration.
"""

from __future__ import annotations

import contextlib
import os
import threading
from dataclasses import dataclass

_ENV_LOCK = threading.Lock()


@contextlib.contextmanager
def explicit_credential_environment():
    """Keep the Aliyun SDK from preferring environment keys over an explicit STS credential.

    ``aliyunsdkcore`` checks ``ALIYUN_ACCESS_KEY_ID`` and
    ``ALIYUN_ACCESS_KEY_SECRET`` before the explicit ``credential`` argument
    when it selects a signer. With temporary credentials that chooses
    ``AccessKeySigner`` and drops ``SecurityToken``. Hide the two keys only
    while ``AcsClient`` captures its signer, then restore them immediately;
    later requests use the credential object captured by the client.
    """
    with _ENV_LOCK:
        saved = {
            name: os.environ.pop(name)
            for name in ("ALIYUN_ACCESS_KEY_ID", "ALIYUN_ACCESS_KEY_SECRET")
            if name in os.environ
        }
        try:
            yield
        finally:
            os.environ.update(saved)


@dataclass(frozen=True)
class Credentials:
    access_key_id: str
    access_key_secret: str
    security_token: str = ""


def read() -> Credentials:
    # Serialize with explicit_credential_environment() so a concurrent client
    # construction cannot hide the keys between our reads.
    with _ENV_LOCK:
        return Credentials(
            access_key_id=os.environ.get("ALIYUN_ACCESS_KEY_ID", "").strip(),
            access_key_secret=os.environ.get("ALIYUN_ACCESS_KEY_SECRET", "").strip(),
            security_token=os.environ.get("ALIYUN_SECURITY_TOKEN", "").strip(),
        )


def read_international() -> Credentials:
    with _ENV_LOCK:
        return Credentials(
            access_key_id=os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID", "").strip(),
            access_key_secret=os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "").strip(),
            security_token=os.environ.get("ALIBABA_CLOUD_SECURITY_TOKEN", "").strip(),
        )


def missing_hint() -> str:
    return (
        "ALIYUN_ACCESS_KEY_ID and ALIYUN_ACCESS_KEY_SECRET "
        "(and ALIYUN_SECURITY_TOKEN for temporary STS credentials)"
    )


def missing_hint_international() -> str:
    return (
        "ALIBABA_CLOUD_ACCESS_KEY_ID and ALIBABA_CLOUD_ACCESS_KEY_SECRET "
        "(and ALIBABA_CLOUD_SECURITY_TOKEN for temporary STS credentials)"
    )
