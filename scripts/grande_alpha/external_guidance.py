from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib.parse import urlparse

EXTERNAL_GUIDANCE_ENV = "GRANDE_ALPHA_EXTERNAL_GUIDANCE_LINKS"


@dataclass(frozen=True)
class ExternalGuidanceLink:
    label: str
    url: str


DEFAULT_EXTERNAL_GUIDANCE_LINKS = (
    ExternalGuidanceLink(
        "Robinhood third-party connection guidance",
        "https://robinhood.com/us/en/support/articles/third-party-connections/",
    ),
)


def _validated_link(value: object) -> ExternalGuidanceLink | None:
    if not isinstance(value, dict):
        return None
    label = str(value.get("label", "")).strip()
    url = str(value.get("url", "")).strip()
    parsed = urlparse(url)
    if (
        not label
        or len(label) > 80
        or any(character in label for character in "\r\n<>\x00")
        or len(url) > 2048
        or any(ord(character) < 32 for character in url)
        or parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return ExternalGuidanceLink(label, url)


def external_guidance_links(raw: str | None = None) -> tuple[ExternalGuidanceLink, ...]:
    """Return product-level links plus validated distributor-configured resources.

    Distributors can provide a JSON array through ``GRANDE_ALPHA_EXTERNAL_GUIDANCE_LINKS``.
    Invalid or non-HTTPS entries are ignored so untrusted configuration cannot inject markup or
    non-web URL handlers into the desktop UI.
    """

    configured = os.environ.get(EXTERNAL_GUIDANCE_ENV, "") if raw is None else raw
    if not configured.strip():
        return DEFAULT_EXTERNAL_GUIDANCE_LINKS
    try:
        values = json.loads(configured)
    except (TypeError, json.JSONDecodeError):
        return DEFAULT_EXTERNAL_GUIDANCE_LINKS
    if not isinstance(values, list):
        return DEFAULT_EXTERNAL_GUIDANCE_LINKS
    custom = tuple(
        link
        for link in (_validated_link(value) for value in values[:8])
        if link is not None
    )
    return DEFAULT_EXTERNAL_GUIDANCE_LINKS + custom
