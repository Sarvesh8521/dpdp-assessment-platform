"""Reusable HTTP client for external API calls.

Provides: retries, exponential backoff, timeouts, rate limit handling,
token refresh, audit logging, structured exceptions, safe response parsing.

Per PHASE2_SPEC: Do not duplicate retry/auth logic in every adapter.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Allowlisted provider hosts — SSRF protection
ALLOWED_HOSTS = frozenset({
    "www.googleapis.com",
    "googleapis.com",
    "analyticsadmin.googleapis.com",
    "tagmanager.googleapis.com",
    "googleads.googleapis.com",
    "graph.facebook.com",
    "api.github.com",
    "api.onetrust.com",
    "app.cookieyes.com",
    "app.securiti.ai",
    "search.googleapis.com",
    "oauth2.googleapis.com",
    "accounts.google.com",
})


class ExternalAPIError(Exception):
    """Base exception for external API errors."""

    def __init__(self, message: str, status_code: int | None = None, provider: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.provider = provider


class RateLimitError(ExternalAPIError):
    """Rate limit hit — should trigger backoff."""

    def __init__(self, retry_after: int | None = None, **kwargs: Any) -> None:
        super().__init__("Rate limit exceeded", status_code=429, **kwargs)
        self.retry_after = retry_after


class AuthenticationError(ExternalAPIError):
    """Authentication failed — token may be expired or revoked."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("Authentication failed", status_code=401, **kwargs)


class ForbiddenError(ExternalAPIError):
    """Access forbidden — insufficient scopes."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("Access forbidden — insufficient scopes", status_code=403, **kwargs)


class NotFoundError(ExternalAPIError):
    """Resource not found."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("Resource not found", status_code=404, **kwargs)


class ForbiddenEndpointError(ExternalAPIError):
    """Attempted to call a forbidden endpoint — this should NEVER happen."""

    def __init__(self, endpoint: str, **kwargs: Any) -> None:
        super().__init__(
            f"SECURITY VIOLATION: Attempted to call forbidden endpoint: {endpoint}",
            **kwargs,
        )


class AuditedHTTPClient:
    """HTTP client with built-in audit logging, retries, and safety controls.

    Every external API call generates an audit entry.
    Never calls forbidden endpoints.
    Validates against allowlisted hosts (SSRF protection).
    """

    def __init__(
        self,
        *,
        provider: str,
        base_url: str = "",
        access_token: str = "",
        forbidden_endpoints: list[str] | None = None,
        obligations_served: list[str] | None = None,
        client_id: str | None = None,
        assessment_id: str | None = None,
        audit_callback: Any | None = None,
    ) -> None:
        settings = get_settings()

        self._provider = provider
        self._base_url = base_url
        self._access_token = access_token
        self._forbidden_endpoints = set(forbidden_endpoints or [])
        self._obligations_served = obligations_served or []
        self._client_id = client_id
        self._assessment_id = assessment_id
        self._audit_callback = audit_callback
        self._call_log: list[dict[str, Any]] = []  # For testing

        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(settings.external_api_timeout_seconds),
            headers=self._build_headers(),
            follow_redirects=False,  # Don't follow arbitrary redirects
        )

    async def __aenter__(self) -> "AuditedHTTPClient":
        await self._client.__aenter__()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self._client.__aexit__(exc_type, exc_val, exc_tb)

    def _build_headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": "DPDP-Assessment-Platform/2.0",
            "Accept": "application/json",
        }
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        return headers

    def _validate_url(self, url: str) -> None:
        """Validate URL against allowlisted hosts (SSRF protection)."""
        full_url = url if url.startswith("http") else f"{self._base_url}{url}"
        parsed = urlparse(full_url)
        if parsed.hostname and parsed.hostname not in ALLOWED_HOSTS:
            raise ExternalAPIError(
                f"Host not in allowlist: {parsed.hostname}",
                provider=self._provider,
            )

    def _check_forbidden(self, endpoint: str) -> None:
        """Check if an endpoint is in the forbidden list."""
        for forbidden in self._forbidden_endpoints:
            if forbidden in endpoint:
                raise ForbiddenEndpointError(endpoint, provider=self._provider)

    async def get(
        self,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        operation: str = "",
    ) -> dict[str, Any]:
        """Make a GET request with audit logging."""
        return await self._request("GET", endpoint, params=params, operation=operation)

    async def post(
        self,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        operation: str = "",
    ) -> dict[str, Any]:
        """Make a POST request with audit logging."""
        return await self._request("POST", endpoint, params=params, json_body=json_body, data=data, operation=operation)

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        operation: str = "",
    ) -> dict[str, Any]:
        """Execute an HTTP request with full safety controls."""
        # Security checks
        self._validate_url(endpoint)
        self._check_forbidden(endpoint)

        # HARD RULE: Never perform write operations
        if method.upper() in ("POST", "PUT", "PATCH", "DELETE"):
            # Only allow POST for OAuth token exchange, not for data modification
            if "oauth" not in endpoint.lower() and "token" not in endpoint.lower():
                raise ExternalAPIError(
                    f"Write operation {method} is forbidden",
                    provider=self._provider,
                )

        start_time = time.monotonic()
        status_code = None
        error_detail = None
        status = "success"

        try:
            response = await self._execute_with_retry(
                method, endpoint, params=params, json_body=json_body, data=data
            )
            status_code = response.status_code

            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                raise RateLimitError(
                    retry_after=int(retry_after) if retry_after else None,
                    provider=self._provider,
                )
            if response.status_code == 401:
                raise AuthenticationError(provider=self._provider)
            if response.status_code == 403:
                raise ForbiddenError(provider=self._provider)
            if response.status_code == 404:
                raise NotFoundError(provider=self._provider)
            if response.status_code >= 500:
                raise ExternalAPIError(
                    f"Server error: {response.status_code}",
                    status_code=response.status_code,
                    provider=self._provider,
                )

            response.raise_for_status()
            return response.json()

        except (RateLimitError, AuthenticationError, ForbiddenError, NotFoundError):
            status = "error"
            raise
        except ExternalAPIError:
            status = "error"
            raise
        except httpx.TimeoutException as exc:
            status = "timeout"
            error_detail = str(exc)
            raise ExternalAPIError(
                "Request timed out", provider=self._provider
            ) from exc
        except httpx.HTTPError as exc:
            status = "error"
            error_detail = str(exc)
            raise ExternalAPIError(
                f"HTTP error: {exc}", provider=self._provider
            ) from exc
        finally:
            duration_ms = int((time.monotonic() - start_time) * 1000)

            # Record in call log (for testing)
            call_record = {
                "method": method,
                "endpoint": endpoint,
                "status": status,
                "status_code": status_code,
                "duration_ms": duration_ms,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self._call_log.append(call_record)

            # Audit callback
            if self._audit_callback:
                try:
                    await self._audit_callback(
                        client_id=self._client_id,
                        assessment_id=self._assessment_id,
                        actor=f"adapter:{self._provider}",
                        integration=self._provider,
                        operation=operation or f"{method} {endpoint}",
                        endpoint=endpoint,
                        status=status,
                        status_code=status_code,
                        duration_ms=duration_ms,
                        obligations_served=self._obligations_served,
                        error_detail=error_detail,
                    )
                except Exception as audit_exc:
                    logger.error("audit_callback_failed", error=str(audit_exc))

    async def _execute_with_retry(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Execute request with retry logic."""
        settings = get_settings()
        last_exc: Exception | None = None

        for attempt in range(settings.external_api_max_retries + 1):
            try:
                response = await self._client.request(
                    method,
                    endpoint,
                    params=params,
                    json=json_body,
                    data=data,
                )

                # Don't retry on client errors (except 429)
                if 400 <= response.status_code < 500 and response.status_code != 429:
                    return response

                # Retry on 429 and 5xx
                if response.status_code in (429,) or response.status_code >= 500:
                    if attempt < settings.external_api_max_retries:
                        wait = min(
                            settings.external_api_backoff_base * (2 ** attempt),
                            settings.external_api_backoff_max,
                        )
                        if response.status_code == 429:
                            retry_after = response.headers.get("Retry-After")
                            if retry_after:
                                wait = max(wait, float(retry_after))
                        logger.warning(
                            "retrying_request",
                            attempt=attempt + 1,
                            wait_seconds=wait,
                            status_code=response.status_code,
                        )
                        import asyncio
                        await asyncio.sleep(wait)
                        continue

                return response

            except httpx.TimeoutException as exc:
                last_exc = exc
                if attempt < settings.external_api_max_retries:
                    wait = settings.external_api_backoff_base * (2 ** attempt)
                    import asyncio
                    await asyncio.sleep(wait)
                    continue
                raise

            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt < settings.external_api_max_retries:
                    wait = settings.external_api_backoff_base * (2 ** attempt)
                    import asyncio
                    await asyncio.sleep(wait)
                    continue
                raise

        # Should not reach here but just in case
        if last_exc:
            raise last_exc
        raise ExternalAPIError("Max retries exceeded", provider=self._provider)

    @property
    def call_log(self) -> list[dict[str, Any]]:
        """Get the log of all API calls made (for testing)."""
        return self._call_log.copy()

    def get_called_endpoints(self) -> list[str]:
        """Get list of all endpoints called (for forbidden endpoint testing)."""
        return [call["endpoint"] for call in self._call_log]

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
