from __future__ import annotations

import json
import sys
import uuid
from dataclasses import replace
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Protocol

EXPECTED_DTK_VERSION = "5.1.11"
DTK_SOURCE_COMMIT = "3c045e11aecd58ab076562f1246931b9dd5d909b"
GUEST_ENDPOINTS = frozenset(
    {
        "tiktok.content_detail",
        "tiktok.author_profile",
        "tiktok.author_posts",
        "tiktok.hashtag_detail",
        "tiktok.hashtag_posts",
    }
)


class TikTokEmbeddedError(RuntimeError):
    """Safe, stable error emitted by the embedded provider."""

    def __init__(self, message: str, error_code: str = "provider_error") -> None:
        super().__init__(message)
        self.error_code = error_code


class RequestGuard(Protocol):
    async def before_request(self, endpoint: str) -> object: ...

    def after_request(self, token: object, succeeded: bool, error_code: str = "") -> None: ...


def _load_dtk_modules() -> dict[str, Any]:
    """Import DTK's platform, signer and transport components without booting DTK."""
    if sys.version_info < (3, 12) or sys.version_info >= (3, 14):
        raise TikTokEmbeddedError(
            "The embedded DTK extra requires Python 3.12 or 3.13.",
            "unsupported_python",
        )
    try:
        import dtk
        from dtk.core.types import BrowserFamily, Platform
        from dtk.platforms.base import ClientProfile
        from dtk.platforms.tiktok.adapter import ADAPTER
        from dtk.platforms.tiktok.endpoints import (
            AUTHOR_POSTS,
            AUTHOR_PROFILE,
            CONTENT_DETAIL,
            HASHTAG_DETAIL,
            HASHTAG_POSTS,
        )
        from dtk.signing.base import RequestSpec as SigningRequestSpec
        from dtk.signing.base import SigningSession, StaticFingerprint
        from dtk.signing.native.signer import NativeSigner
        from dtk.transport.base import Fingerprint, RequestSpec as TransportRequestSpec
        from dtk.transport.base import TransportIdentity
        from dtk.transport.emulation import known_majors
        from dtk.transport.wreq_transport import WreqTransport
    except ImportError as exc:
        missing = getattr(exc, "name", None) or type(exc).__name__
        raise TikTokEmbeddedError(
            "DTK is not installed. Use Python 3.12/3.13 and install "
            "the tiktok-embedded optional dependency. Missing module: " + str(missing),
            "dtk_extra_missing",
        ) from None

    version = getattr(dtk, "__version__", "")
    if version != EXPECTED_DTK_VERSION:
        raise TikTokEmbeddedError(
            "DTK version mismatch: expected "
            + EXPECTED_DTK_VERSION
            + ", found "
            + (version or "unknown")
            + ". Review the pinned integration before upgrading.",
            "dtk_version_mismatch",
        )
    return {
        "BrowserFamily": BrowserFamily,
        "LoginNeed": LoginNeed,
        "Platform": Platform,
        "ADAPTER": ADAPTER,
        "AUTHOR_POSTS": AUTHOR_POSTS,
        "AUTHOR_PROFILE": AUTHOR_PROFILE,
        "CONTENT_DETAIL": CONTENT_DETAIL,
        "HASHTAG_DETAIL": HASHTAG_DETAIL,
        "HASHTAG_POSTS": HASHTAG_POSTS,
        "ClientProfile": ClientProfile,
        "SigningRequestSpec": SigningRequestSpec,
        "SigningSession": SigningSession,
        "StaticFingerprint": StaticFingerprint,
        "NativeSigner": NativeSigner,
        "Fingerprint": Fingerprint,
        "TransportRequestSpec": TransportRequestSpec,
        "TransportIdentity": TransportIdentity,
        "known_majors": known_majors,
        "WreqTransport": WreqTransport,
    }


def _get(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _content_record(content: Any) -> dict[str, Any]:
    stats = _get(content, "stats")
    author = _get(content, "author")
    created_at = _get(content, "created_at")
    if isinstance(created_at, datetime):
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        published_at = created_at.astimezone(timezone.utc).isoformat()
    else:
        published_at = str(created_at) if created_at else None

    kind = _get(content, "kind", "unknown")
    kind = getattr(kind, "value", kind)
    tags = _get(content, "tags", []) or []
    return {
        "content_id": str(_get(content, "content_id", "") or ""),
        "content_url": str(_get(content, "web_url", "") or ""),
        "content_type": str(kind or "unknown"),
        "published_at": published_at,
        "caption": str(
            _get(content, "description", "") or _get(content, "title", "") or ""
        )[:4000],
        "hashtags": [str(tag).lstrip("#") for tag in tags],
        "author_id": str(_get(author, "uid", "") or ""),
        "author_handle": str(_get(author, "unique_id", "") or ""),
        "views": _get(stats, "play_count"),
        "likes": _get(stats, "digg_count"),
        "comments": _get(stats, "comment_count"),
        "shares": _get(stats, "share_count"),
        "saves": _get(stats, "collect_count"),
    }


class DTKEmbeddedClient:
    """Narrow in-process DTK client for anonymous public TikTok endpoints.

    No DTK API, worker, MCP, database engine, Redis client, browser-RPC service
    or imported login identity is started. A single anonymous wreq transport
    and DTK's native signer are used only for the allowlisted endpoints.
    """

    def __init__(
        self,
        *,
        request_guard: RequestGuard | None = None,
        timeout_seconds: float = 25.0,
        proxy_url: str | None = None,
        max_requests_per_run: int = 40,
    ) -> None:
        if max_requests_per_run < 1:
            raise ValueError("max_requests_per_run must be >= 1")
        modules = _load_dtk_modules()
        self._modules = modules
        self._adapter = modules["ADAPTER"]
        self._guard = request_guard
        self._timeout = timeout_seconds
        self._max_requests = max_requests_per_run
        self._requests = 0
        self._closed = False

        browser_family = modules["BrowserFamily"].CHROME
        majors = modules["known_majors"](browser_family)
        if not majors:
            raise TikTokEmbeddedError(
                "The installed wreq package exposes no Chrome TLS profiles.",
                "no_tls_profile",
            )
        browser_major = max(majors)
        version = str(browser_major) + ".0.0.0"
        user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/" + version + " Safari/537.36"
        )
        self._fingerprint = modules["Fingerprint"](
            browser_family=browser_family,
            browser_major=browser_major,
            user_agent=user_agent,
            platform="Win32",
            screen="1920x1080",
            language="en-US",
            timezone="America/Los_Angeles",
            hardware_concurrency=8,
            device_memory=8,
        )
        self._signing_fingerprint = modules["StaticFingerprint"](
            user_agent=user_agent,
            browser_platform="Win32",
            screen_width=1920,
            screen_height=1080,
        )
        self._identity = modules["TransportIdentity"](
            id="fashion-radar-guest-" + str(uuid.uuid4()),
            platform=modules["Platform"].TIKTOK,
            fingerprint=self._fingerprint,
            proxy_url=proxy_url or None,
            cookies={},
        )
        self._signer = modules["NativeSigner"](
            modules["Platform"].TIKTOK,
            fill_ms_token=False,
        )
        self._transport = modules["WreqTransport"](
            default_timeout=timeout_seconds,
            connect_timeout=min(10.0, timeout_seconds),
            max_clients=1,
        )
        self._endpoints = {
            "content_detail": modules["CONTENT_DETAIL"],
            "author_profile": modules["AUTHOR_PROFILE"],
            "author_posts": modules["AUTHOR_POSTS"],
            "hashtag_detail": modules["HASHTAG_DETAIL"],
            "hashtag_posts": modules["HASHTAG_POSTS"],
        }

    @property
    def requests_made(self) -> int:
        return self._requests

    @property
    def identity_mode(self) -> str:
        return "anonymous_guest_no_cookies"

    async def _request_json(
        self, endpoint: str, market_code: str, **params: Any
    ) -> dict[str, Any]:
        if self._closed:
            raise TikTokEmbeddedError("The TikTok provider is closed.", "client_closed")
        if endpoint not in GUEST_ENDPOINTS:
            raise TikTokEmbeddedError(
                "Endpoint is not in the embedded guest allowlist: " + endpoint,
                "endpoint_not_allowed",
            )
        if self._requests >= self._max_requests:
            raise TikTokEmbeddedError(
                "Per-run TikTok request budget reached.", "run_budget_exhausted"
            )

        endpoint_spec = self._adapter.endpoints.specs[endpoint]
        login_need = endpoint_spec.login_for(params)
        if (
            getattr(login_need, "value", str(login_need)) != "none"
            or endpoint_spec.session_bound
        ):
            raise TikTokEmbeddedError(
                endpoint + " is unavailable in anonymous guest mode.",
                "login_required",
            )

        guard_token: object | None = None
        if self._guard is not None:
            guard_token = await self._guard.before_request(endpoint)
        self._requests += 1
        succeeded = False
        error_code = "request_failed"
        try:
            language = {"US": "en-US", "CA": "en-CA", "GB": "en-GB"}[market_code]
            profile = replace(
                self._adapter.profile_for(self._fingerprint),
                browser_language=language,
                language="en",
                region=market_code,
            )
            platform_request = self._adapter.build_request(
                endpoint, profile=profile, **params
            )
            signing_request = self._modules["SigningRequestSpec"](
                method=platform_request["method"],
                url=platform_request["url"],
                params=platform_request.get("params"),
                headers=platform_request.get("headers"),
                body=platform_request.get("body"),
            )
            signed = await self._signer.sign(
                signing_request,
                self._signing_fingerprint,
                self._modules["SigningSession"](cookies={}),
            )
            transport_request = self._modules["TransportRequestSpec"](
                url=signed.signed_url(platform_request["url"]),
                method=platform_request["method"],
                params=None,
                headers={
                    **(platform_request.get("headers") or {}),
                    **dict(signed.headers),
                },
                body=platform_request.get("body"),
                endpoint=endpoint,
            )
            response = await self._transport.request(
                self._identity, transport_request, timeout=self._timeout
            )
            if not 200 <= response.status < 300:
                if response.status == 429:
                    error_code = "rate_limited"
                elif response.status in {401, 403}:
                    error_code = "upstream_restricted"
                else:
                    error_code = "http_" + str(response.status)
                raise TikTokEmbeddedError(
                    "TikTok returned HTTP "
                    + str(response.status)
                    + " for "
                    + endpoint
                    + ".",
                    error_code,
                )
            try:
                payload = json.loads(response.text)
            except (ValueError, TypeError):
                error_code = "invalid_json"
                raise TikTokEmbeddedError(
                    "TikTok returned a non-JSON response for " + endpoint + ".",
                    error_code,
                ) from None
            if not isinstance(payload, dict):
                error_code = "invalid_response"
                raise TikTokEmbeddedError(
                    "TikTok returned an unexpected response shape.",
                    error_code,
                )
            risk = self._adapter.detect_risk_control(payload)
            if risk:
                error_code = "risk_control"
                raise TikTokEmbeddedError(
                    "TikTok returned a risk-control response for " + endpoint + ".",
                    error_code,
                )
            succeeded = True
            error_code = ""
            return payload
        except TikTokEmbeddedError:
            raise
        except Exception as exc:
            error_code = type(exc).__name__.lower()
            raise TikTokEmbeddedError(
                "Embedded TikTok request failed for "
                + endpoint
                + ": "
                + type(exc).__name__
                + ".",
                error_code,
            ) from None
        finally:
            if self._guard is not None and guard_token is not None:
                self._guard.after_request(guard_token, succeeded, error_code)

    async def _content_detail(self, content_id: str, market_code: str) -> dict[str, Any]:
        endpoint = self._endpoints["content_detail"]
        payload = await self._request_json(endpoint, market_code, item_id=content_id)
        content = self._adapter.parse_content(
            payload, fetched_at=datetime.now(timezone.utc)
        )
        return _content_record(content)

    async def _author_posts(
        self, handle: str, market_code: str, max_items: int
    ) -> list[dict[str, Any]]:
        profile_endpoint = self._endpoints["author_profile"]
        profile_payload = await self._request_json(
            profile_endpoint, market_code, unique_id=handle.lstrip("@")
        )
        author = self._adapter.parse_author(profile_payload)
        sec_uid = getattr(author, "sec_uid", None)
        if not sec_uid:
            raise TikTokEmbeddedError(
                "The public author profile did not return a usable sec_uid.",
                "author_id_missing",
            )

        endpoint = self._endpoints["author_posts"]
        items: list[dict[str, Any]] = []
        cursor: str | None = None
        max_pages = min(5, max(1, (max_items + 19) // 20))
        for _ in range(max_pages):
            remaining = max_items - len(items)
            if remaining <= 0:
                break
            payload = await self._request_json(
                endpoint,
                market_code,
                sec_uid=sec_uid,
                cursor=cursor,
                count=min(20, remaining),
            )
            page = self._adapter.parse_author_posts(
                payload, fetched_at=datetime.now(timezone.utc)
            )
            items.extend(_content_record(item) for item in page.items)
            if len(items) >= max_items or not page.has_more or not page.cursor:
                break
            if page.cursor == cursor:
                break
            cursor = page.cursor
        return items[:max_items]

    async def _hashtag_posts(
        self, tag: str, market_code: str, max_items: int
    ) -> list[dict[str, Any]]:
        detail_endpoint = self._endpoints["hashtag_detail"]
        detail_payload = await self._request_json(
            detail_endpoint, market_code, challenge_name=tag.lstrip("#")
        )
        hashtag = self._adapter.parse_hashtag_detail(detail_payload)
        challenge_id = getattr(hashtag, "hashtag_id", None)
        if not challenge_id:
            raise TikTokEmbeddedError(
                "The public hashtag detail did not return a numeric ID.",
                "hashtag_id_missing",
            )

        endpoint = self._endpoints["hashtag_posts"]
        items: list[dict[str, Any]] = []
        cursor: str | None = None
        max_pages = min(5, max(1, (max_items + 19) // 20))
        for _ in range(max_pages):
            remaining = max_items - len(items)
            if remaining <= 0:
                break
            payload = await self._request_json(
                endpoint,
                market_code,
                challenge_id=str(challenge_id),
                cursor=cursor,
                count=min(20, remaining),
            )
            page = self._adapter.parse_hashtag_posts(
                payload, fetched_at=datetime.now(timezone.utc)
            )
            items.extend(_content_record(item) for item in page.items)
            if len(items) >= max_items or not page.has_more or not page.cursor:
                break
            if page.cursor == cursor:
                break
            cursor = page.cursor
        return items[:max_items]

    async def fetch_seed(
        self, seed_type: str, seed_value: str, market_code: str, max_items: int
    ) -> list[dict[str, Any]]:
        if seed_type == "video_id":
            return [await self._content_detail(seed_value, market_code)]
        if seed_type == "creator_handle":
            return await self._author_posts(seed_value, market_code, max_items)
        if seed_type == "hashtag":
            return await self._hashtag_posts(seed_value, market_code, max_items)
        raise TikTokEmbeddedError(
            "Unsupported guest seed type: " + seed_type, "invalid_seed_type"
        )

    async def aclose(self) -> None:
        if not self._closed:
            await self._transport.close()
            self._closed = True


__all__ = [
    "DTK_SOURCE_COMMIT",
    "DTKEmbeddedClient",
    "EXPECTED_DTK_VERSION",
    "GUEST_ENDPOINTS",
    "TikTokEmbeddedError",
]
