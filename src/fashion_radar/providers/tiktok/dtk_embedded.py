from __future__ import annotations

import errno as errno_codes
import json
import socket
import ssl
import sys
import uuid
from collections.abc import Mapping
from dataclasses import replace
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

    def __init__(
        self,
        message: str,
        error_code: str = "provider_error",
        *,
        diagnostic: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.diagnostic = dict(diagnostic) if diagnostic is not None else None


_DIAGNOSTIC_VERSION = 1


def _diagnostic(
    connection_stage: str,
    exception_type: str | None,
    error_code: str,
    retryable: bool,
) -> dict[str, Any]:
    """Build a small diagnostic record without exception text or request data."""
    return {
        "diagnostic_version": _DIAGNOSTIC_VERSION,
        "connection_stage": connection_stage,
        "exception_type": exception_type,
        "error_code": error_code,
        "retryable": retryable,
    }


def _safe_exception_type(exc: BaseException) -> str:
    """Keep only a bounded Python type name; never serialize exception messages."""
    raw_name = type(exc).__name__
    safe_name = "".join(
        char
        for char in raw_name
        if char.isascii() and (char.isalnum() or char in "._")
    )
    return safe_name[:80] or "UnknownException"


def _exception_chain(exc: BaseException) -> list[BaseException]:
    """Follow standard and DTK transport causes, with cycle and depth guards."""
    pending = [exc]
    visited: set[int] = set()
    chain: list[BaseException] = []
    while pending and len(chain) < 16:
        current = pending.pop(0)
        if id(current) in visited:
            continue
        visited.add(id(current))
        chain.append(current)
        try:
            transport_cause = getattr(current, "cause", None)
        except Exception:
            transport_cause = None
        for linked in (
            transport_cause,
            current.__cause__,
            current.__context__,
        ):
            if isinstance(linked, BaseException) and id(linked) not in visited:
                pending.append(linked)
    return chain


def _diagnose_network_exception(exc: BaseException) -> dict[str, Any]:
    """Classify only explicit low-level evidence; ambiguous failures stay unknown."""
    chain = _exception_chain(exc)
    for current in reversed(chain):
        exception_type = _safe_exception_type(current)
        type_name = type(current).__name__
        error_number = getattr(current, "errno", None)
        winerror = getattr(current, "winerror", None)

        if isinstance(current, (socket.gaierror, socket.herror)) or type_name in {
            "DNSError",
            "DNSLookupError",
        } or winerror in {11001, 11002, 11003, 11004}:
            temporary = (
                error_number == getattr(socket, "EAI_AGAIN", object())
                or winerror == 11002
            )
            return _diagnostic(
                "dns",
                exception_type,
                "dns_temporary_failure" if temporary else "dns_resolution_failed",
                temporary,
            )

        if isinstance(current, ssl.SSLError) or type_name in {"TlsError", "SSLError"}:
            return _diagnostic("tls", exception_type, "tls_error", False)

        if type_name == "ConnectTimeout":
            return _diagnostic("tcp", exception_type, "connect_timeout", True)

        if type_name == "ReadTimeout":
            return _diagnostic("transport", exception_type, "read_timeout", True)

        if type_name == "ProxyConnectionError":
            return _diagnostic("tcp", exception_type, "proxy_connection_error", True)

        if isinstance(current, ConnectionRefusedError) or winerror == 10061:
            return _diagnostic("tcp", exception_type, "tcp_connection_refused", False)

        if isinstance(current, ConnectionResetError) or winerror == 10054:
            return _diagnostic("tcp", exception_type, "tcp_connection_reset", True)

        if isinstance(current, ConnectionAbortedError) or winerror == 10053:
            return _diagnostic("tcp", exception_type, "tcp_connection_aborted", True)

        if isinstance(current, BrokenPipeError):
            return _diagnostic("tcp", exception_type, "tcp_broken_pipe", True)

        tcp_errno_codes = {
            getattr(errno_codes, "ENETUNREACH", None): "tcp_network_unreachable",
            getattr(errno_codes, "EHOSTUNREACH", None): "tcp_host_unreachable",
            getattr(errno_codes, "ENETDOWN", None): "tcp_network_unreachable",
            getattr(errno_codes, "EHOSTDOWN", None): "tcp_host_unreachable",
        }
        tcp_errno_codes.pop(None, None)
        if error_number in tcp_errno_codes or winerror in {10051, 10065}:
            code = tcp_errno_codes.get(error_number, "tcp_network_unreachable")
            return _diagnostic("tcp", exception_type, code, True)

        if error_number == getattr(errno_codes, "ETIMEDOUT", object()) or winerror == 10060:
            return _diagnostic("transport", exception_type, "transport_timeout", True)

        if isinstance(current, TimeoutError) or type_name in {"Timeout", "TimeoutError"}:
            return _diagnostic("transport", exception_type, "transport_timeout", True)

        if isinstance(current, ConnectionError):
            return _diagnostic("unknown", exception_type, "connection_error", False)

        if type_name in {
            "BodyError",
            "DecodingError",
            "RedirectError",
            "RequestError",
            "UpgradeError",
        }:
            return _diagnostic("transport", exception_type, "transport_error", False)

    leaf = chain[-1] if chain else exc
    leaf_type = _safe_exception_type(leaf)
    return _diagnostic("unknown", leaf_type, "transport_error", False)


def _diagnose_http_status(status: int) -> dict[str, Any]:
    """Describe an HTTP response without confusing it with a socket failure."""
    retryable = status == 408 or 500 <= status < 600
    return _diagnostic("http", None, "http_" + str(status), retryable)


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
        "Platform": Platform,
        "ADAPTER": ADAPTER,
        "AUTHOR_POSTS": AUTHOR_POSTS,
        "AUTHOR_PROFILE": AUTHOR_PROFILE,
        "CONTENT_DETAIL": CONTENT_DETAIL,
        "HASHTAG_DETAIL": HASHTAG_DETAIL,
        "HASHTAG_POSTS": HASHTAG_POSTS,
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
        phase = "request_preparation"
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
            phase = "signing"
            signed = await self._signer.sign(
                signing_request,
                self._signing_fingerprint,
                self._modules["SigningSession"](cookies={}),
            )
            phase = "request_preparation"
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
            phase = "transport"
            response = await self._transport.request(
                self._identity, transport_request, timeout=self._timeout
            )
            phase = "response"
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
                    diagnostic=_diagnose_http_status(response.status),
                )
            try:
                payload = json.loads(response.text)
            except (ValueError, TypeError):
                error_code = "invalid_json"
                raise TikTokEmbeddedError(
                    "TikTok returned a non-JSON response for " + endpoint + ".",
                    error_code,
                    diagnostic=_diagnostic("http", None, "invalid_json", False),
                ) from None
            if not isinstance(payload, dict):
                error_code = "invalid_response"
                raise TikTokEmbeddedError(
                    "TikTok returned an unexpected response shape.",
                    error_code,
                    diagnostic=_diagnostic("http", None, "invalid_response", False),
                )
            risk = self._adapter.detect_risk_control(payload)
            if risk:
                error_code = "risk_control"
                raise TikTokEmbeddedError(
                    "TikTok returned a risk-control response for " + endpoint + ".",
                    error_code,
                    diagnostic=_diagnostic("http", None, "risk_control", False),
                )
            succeeded = True
            error_code = ""
            return payload
        except TikTokEmbeddedError:
            raise
        except Exception as exc:
            if phase == "transport":
                diagnostic = _diagnose_network_exception(exc)
            elif phase == "response":
                diagnostic = _diagnostic(
                    "http",
                    _safe_exception_type(exc),
                    "response_processing_error",
                    False,
                )
            else:
                diagnostic = _diagnostic(
                    "not_started",
                    _safe_exception_type(exc),
                    phase + "_error",
                    False,
                )
            error_code = str(diagnostic["error_code"])
            raise TikTokEmbeddedError(
                "Embedded TikTok request failed for "
                + endpoint
                + ": "
                + error_code
                + ".",
                error_code,
                diagnostic=diagnostic,
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
