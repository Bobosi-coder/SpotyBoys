from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone

_VERSION_RE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_component(value: str) -> str:
    cleaned = _VERSION_RE.sub("-", value.strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned)
    return cleaned.strip("-")


def make_default_version(label: str | None = None, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    version = now.strftime("v%Y%m%d-%H%M%S")
    if label:
        safe_label = sanitize_component(label)
        if safe_label:
            version = f"{version}-{safe_label}"
    return version


def resolve_version(
    *,
    explicit: str | None = None,
    env_var: str | None = None,
    label: str | None = None,
) -> str:
    candidate = explicit
    if not candidate and env_var:
        candidate = os.environ.get(env_var)
    if candidate:
        safe = sanitize_component(candidate)
        if not safe:
            raise ValueError(f"Resolved version for {env_var or 'explicit value'} is empty.")
        return safe
    return make_default_version(label=label)


def join_s3_key(*parts: str) -> str:
    return "/".join(part.strip("/") for part in parts if part and part.strip("/"))


def build_s3_uri(bucket: str, key: str) -> str:
    return f"s3://{bucket}/{key.lstrip('/')}"


def split_s3_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("s3://"):
        raise ValueError(f"Unsupported S3 URI: {uri}")
    without_scheme = uri[5:]
    bucket, _, key = without_scheme.partition("/")
    return bucket, key


@dataclass(frozen=True)
class ReleaseLocation:
    bucket: str
    prefix: str
    version: str

    @property
    def versioned_prefix(self) -> str:
        return join_s3_key(self.prefix, self.version)

    @property
    def uri(self) -> str:
        return build_s3_uri(self.bucket, self.versioned_prefix)

