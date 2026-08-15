"""HTTP client for the Realtime Trains Next Generation API."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class RTTError(RuntimeError):
    """A friendly error raised for an unsuccessful RTT API request."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class RTTResponse:
    """Decoded API response plus useful response metadata."""

    data: Any
    status: int
    rate_limits: dict[str, str]


def _api_value(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


class RTTClient:
    """Synchronous, dependency-free RTT API client.

    ``token_type='auto'`` first treats the configured token as an access token.
    If ``/api/info`` returns 401, it exchanges it as a refresh token and retries.
    """

    def __init__(
        self,
        token: str,
        *,
        base_url: str = "https://data.rtt.io",
        api_version: str | None = None,
        token_type: str = "auto",
        timeout: float = 20.0,
        max_retries: int = 1,
    ) -> None:
        if not token.strip():
            raise ValueError("An RTT token is required")
        if token_type not in {"auto", "access", "refresh"}:
            raise ValueError("token_type must be auto, access, or refresh")
        self.base_url = base_url.rstrip("/")
        self.api_version = api_version
        self.token_type = token_type
        self.timeout = timeout
        self.max_retries = max_retries
        self._configured_token = token.strip()
        self._access_token: str | None = (
            self._configured_token if token_type in {"auto", "access"} else None
        )
        self._access_token_valid_until: str | None = None

    def _headers(self, token: str) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "rtt-python-example/0.1",
        }
        if self.api_version:
            headers["Version"] = self.api_version
        return headers

    @staticmethod
    def _rate_limits(headers: Mapping[str, str]) -> dict[str, str]:
        return {
            key: value
            for key, value in headers.items()
            if key.lower().startswith("x-ratelimit-") or key.lower() == "retry-after"
        }

    def _send(
        self, path: str, params: Mapping[str, Any] | None, token: str
    ) -> RTTResponse:
        clean_params = {
            key: _api_value(value)
            for key, value in (params or {}).items()
            if value is not None
        }
        url = f"{self.base_url}/{path.lstrip('/')}"
        if clean_params:
            url = f"{url}?{urlencode(clean_params)}"

        for attempt in range(self.max_retries + 1):
            request = Request(url, headers=self._headers(token), method="GET")
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    raw = response.read()
                    data = json.loads(raw) if raw else None
                    return RTTResponse(
                        data=data,
                        status=response.status,
                        rate_limits=self._rate_limits(response.headers),
                    )
            except HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                if exc.code == 429 and attempt < self.max_retries:
                    retry_after = float(exc.headers.get("Retry-After", "1"))
                    time.sleep(min(max(retry_after, 0.0), 60.0))
                    continue
                detail = body
                try:
                    parsed = json.loads(body)
                    detail = parsed.get("message") or parsed.get("detail") or body
                except (json.JSONDecodeError, AttributeError):
                    pass
                message = f"RTT API returned HTTP {exc.code}"
                if detail.strip():
                    message += f": {detail.strip()}"
                raise RTTError(message, status=exc.code) from exc
            except URLError as exc:
                raise RTTError(f"Could not reach RTT API: {exc.reason}") from exc

        raise AssertionError("unreachable")

    def exchange_refresh_token(self) -> RTTResponse:
        """Exchange the configured refresh token for a short-lived access token."""
        response = self._send(
            "/api/get_access_token", None, self._configured_token
        )
        if not isinstance(response.data, dict) or not response.data.get("token"):
            raise RTTError("RTT token exchange returned no access token")
        self._access_token = str(response.data["token"])
        self._access_token_valid_until = response.data.get("validUntil")
        return response

    def request(
        self, path: str, params: Mapping[str, Any] | None = None
    ) -> RTTResponse:
        """Make an authenticated GET request to any RTT endpoint."""
        if self.token_type == "refresh" and self._access_token is None:
            self.exchange_refresh_token()
        token = self._access_token or self._configured_token
        try:
            return self._send(path, params, token)
        except RTTError as exc:
            if self.token_type == "auto" and exc.status == 401:
                self.token_type = "refresh"
                self.exchange_refresh_token()
                return self._send(path, params, self._access_token or "")
            if self.token_type == "refresh" and exc.status == 401:
                self.exchange_refresh_token()
                return self._send(path, params, self._access_token or "")
            raise

    def info(self) -> RTTResponse:
        return self.request("/api/info")

    def location(
        self,
        code: str,
        *,
        time_from: str | None = None,
        time_to: str | None = None,
        time_window: int | None = None,
        filter_from: str | None = None,
        filter_to: str | None = None,
        detailed: bool = False,
        network_rail: bool = True,
        stp_filter: str | None = None,
    ) -> RTTResponse:
        path = "/gb-nr/location" if network_rail else "/rtt/location"
        return self.request(
            path,
            {
                "code": code,
                "timeFrom": time_from,
                "timeTo": time_to,
                "timeWindow": time_window,
                "filterFrom": filter_from,
                "filterTo": filter_to,
                "detailed": detailed,
                "stpFilter": stp_filter if network_rail else None,
            },
        )

    def service(
        self,
        *,
        unique_identity: str | None = None,
        identity: str | None = None,
        departure_date: str | None = None,
        namespace: str = "gb-nr",
        detailed: bool = False,
        network_rail: bool = True,
    ) -> RTTResponse:
        if not unique_identity and not (identity and departure_date):
            raise ValueError(
                "Supply unique_identity, or both identity and departure_date"
            )
        path = "/gb-nr/service" if network_rail else "/rtt/service"
        return self.request(
            path,
            {
                "uniqueIdentity": unique_identity,
                "namespace": None if network_rail else namespace,
                "identity": identity,
                "departureDate": departure_date,
                "detailed": detailed,
            },
        )

    def stops(self) -> RTTResponse:
        return self.request("/data/stops")

    def locations(self) -> RTTResponse:
        return self.request("/data/locations_ungrouped")

    def allocations_by_service(self, departure_date: str, toc: str) -> RTTResponse:
        return self.request(
            "/gb-nr/allocations/by-service",
            {"departureDate": departure_date, "toc": toc},
        )

    def allocations_by_class(
        self, departure_date: str, rolling_stock_class: str
    ) -> RTTResponse:
        return self.request(
            "/gb-nr/allocations/by-class",
            {"departureDate": departure_date, "class": rolling_stock_class},
        )
