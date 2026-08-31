"""HTTPS-ready, dependency-free HTTP API for a ChatGPT GPT Action."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit

from .cli import load_dotenv
from .client import RTTClient, RTTError
from .rail_assistant import RTTRailTools


MAX_RESPONSE_BYTES = 950_000


def _one(params: Mapping[str, list[str]], name: str, default: str | None = None) -> str | None:
    values = params.get(name)
    return values[0].strip() if values and values[0].strip() else default


def _integer(
    params: Mapping[str, list[str]], name: str, default: int, minimum: int, maximum: int
) -> int:
    raw = _one(params, name)
    try:
        value = default if raw is None else int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _boolean(params: Mapping[str, list[str]], name: str, default: bool = False) -> bool:
    raw = _one(params, name)
    if raw is None:
        return default
    if raw.casefold() in {"true", "1", "yes"}:
        return True
    if raw.casefold() in {"false", "0", "no"}:
        return False
    raise ValueError(f"{name} must be true or false")


def build_openapi_schema(base_url: str) -> dict[str, Any]:
    """Return the schema to paste/import in the GPT Action editor."""
    server = base_url.rstrip("/")
    error = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}, "error": {"type": "string"}},
        "required": ["ok", "error"],
    }
    json_response = {
        "description": "Successful RTT response",
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "ok": {
                            "type": "boolean",
                            "description": "Whether the request completed successfully.",
                        }
                    },
                    "additionalProperties": True,
                }
            }
        },
    }
    error_responses = {
        "400": {
            "description": "Invalid query",
            "content": {"application/json": {"schema": error}},
        },
        "401": {
            "description": "Missing or incorrect action API key",
            "content": {"application/json": {"schema": error}},
        },
        "502": {
            "description": "RTT upstream API error",
            "content": {"application/json": {"schema": error}},
        },
    }
    station = {
        "name": "station",
        "in": "query",
        "required": True,
        "description": "UK station name or CRS code, such as Bristol Temple Meads or BRI.",
        "schema": {"type": "string"},
    }
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Realtime Trains Rail Service Action",
            "description": (
                "Read live and scheduled UK rail services, allocations and Know Your Train "
                "coach data from the Realtime Trains API. Data can be absent or change."
            ),
            "version": "1.0.0",
        },
        "servers": [{"url": server}],
        "paths": {
            "/v1/departures": {
                "get": {
                    "operationId": "getNextDepartures",
                    "summary": "Get the next passenger departures",
                    "description": "Returns times, destination, platform, allocation and live status.",
                    "parameters": [
                        station,
                        {
                            "name": "count",
                            "in": "query",
                            "description": "Number of departures to return.",
                            "schema": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                        },
                        {
                            "name": "minutes",
                            "in": "query",
                            "description": "How far ahead to search in minutes.",
                            "schema": {"type": "integer", "minimum": 1, "maximum": 1439, "default": 180},
                        },
                    ],
                    "responses": {"200": json_response, **error_responses},
                }
            },
            "/v1/services": {
                "get": {
                    "operationId": "searchStationServices",
                    "summary": "Search arrivals or departures at a station",
                    "description": (
                        "Find a particular working or candidates for a forming service. Returns "
                        "uniqueIdentity values that can be passed to getServiceDetails."
                    ),
                    "parameters": [
                        station,
                        {
                            "name": "time_from",
                            "in": "query",
                            "description": "ISO-8601 start datetime. Omit to start now.",
                            "schema": {"type": "string", "format": "date-time"},
                        },
                        {
                            "name": "time_to",
                            "in": "query",
                            "description": "ISO-8601 end datetime. Do not combine with minutes.",
                            "schema": {"type": "string", "format": "date-time"},
                        },
                        {
                            "name": "minutes",
                            "in": "query",
                            "description": "Window length when time_to is omitted.",
                            "schema": {"type": "integer", "minimum": 1, "maximum": 1439, "default": 180},
                        },
                        {
                            "name": "filter_from",
                            "in": "query",
                            "description": "Only services previously calling at this station name or CRS code.",
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "filter_to",
                            "in": "query",
                            "description": "Only services subsequently calling at this station name or CRS code.",
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "movement",
                            "in": "query",
                            "description": "Whether to return arrivals, departures or both.",
                            "schema": {"type": "string", "enum": ["all", "arrivals", "departures"], "default": "all"},
                        },
                        {
                            "name": "count",
                            "in": "query",
                            "description": "Maximum services to return.",
                            "schema": {"type": "integer", "minimum": 1, "maximum": 40, "default": 20},
                        },
                    ],
                    "responses": {"200": json_response, **error_responses},
                }
            },
            "/v1/service": {
                "get": {
                    "operationId": "getServiceDetails",
                    "summary": "Get detailed data for one train service",
                    "description": (
                        "Returns calls, allocation identities, associations and Know Your Train "
                        "coach or formation data when RTT supplies it."
                    ),
                    "parameters": [
                        {
                            "name": "unique_identity",
                            "in": "query",
                            "required": True,
                            "description": "The exact uniqueIdentity from searchStationServices.",
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": json_response, **error_responses},
                }
            },
            "/v1/info": {
                "get": {
                    "operationId": "getRttApiInfo",
                    "summary": "Get RTT API version and entitlements",
                    "description": "Returns the active RTT API metadata for diagnostics.",
                    "responses": {"200": json_response, **error_responses},
                }
            },
        },
    }


@dataclass(frozen=True)
class ActionResponse:
    status: int
    body: Any
    content_type: str = "application/json; charset=utf-8"


class ActionApplication:
    """Request dispatcher, separated from HTTP transport for reliable testing."""

    def __init__(self, tools: RTTRailTools, *, api_key: str, base_url: str) -> None:
        if not api_key.strip():
            raise ValueError("ACTION_API_KEY is required")
        self.tools = tools
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self._lock = threading.RLock()

    def _authorized(self, headers: Mapping[str, str]) -> bool:
        authorization = headers.get("Authorization", "")
        supplied = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        supplied = supplied or headers.get("X-Action-Key", "").strip()
        return bool(supplied) and secrets.compare_digest(supplied, self.api_key)

    def dispatch(
        self, method: str, path: str, params: Mapping[str, list[str]], headers: Mapping[str, str]
    ) -> ActionResponse:
        if method != "GET":
            return ActionResponse(405, {"ok": False, "error": "Method not allowed"})
        if path == "/health":
            return ActionResponse(200, {"ok": True, "service": "rtt-gpt-action"})
        if path == "/openapi.json":
            return ActionResponse(200, build_openapi_schema(self.base_url))
        if path == "/privacy":
            body = (
                "<!doctype html><title>RTT Rail Action privacy</title>"
                "<h1>Privacy</h1><p>This private action forwards rail queries to Realtime Trains. "
                "It does not require or store an OpenAI API key. Server access logs may contain "
                "request time, IP address and query parameters according to the hosting provider.</p>"
            )
            return ActionResponse(200, body, "text/html; charset=utf-8")
        if not path.startswith("/v1/"):
            return ActionResponse(404, {"ok": False, "error": "Not found"})
        if not self._authorized(headers):
            return ActionResponse(401, {"ok": False, "error": "Unauthorized"})
        try:
            with self._lock:
                if path == "/v1/departures":
                    result = self.tools.next_departures(
                        station=_one(params, "station") or _required("station"),
                        count=_integer(params, "count", 5, 1, 20),
                        minutes=_integer(params, "minutes", 180, 1, 1439),
                    )
                elif path == "/v1/services":
                    time_to = _one(params, "time_to")
                    minutes = None if time_to else _integer(params, "minutes", 180, 1, 1439)
                    movement = _one(params, "movement", "all") or "all"
                    if movement not in {"all", "arrivals", "departures"}:
                        raise ValueError("movement must be all, arrivals, or departures")
                    result = self.tools.search_station_services(
                        station=_one(params, "station") or _required("station"),
                        time_from=_one(params, "time_from"),
                        time_to=time_to,
                        minutes=minutes,
                        filter_from=_one(params, "filter_from"),
                        filter_to=_one(params, "filter_to"),
                        movement=movement,
                        count=_integer(params, "count", 20, 1, 40),
                    )
                elif path == "/v1/service":
                    result = self.tools.get_service_details(
                        _one(params, "unique_identity") or _required("unique_identity")
                    )
                elif path == "/v1/info":
                    result = self.tools.get_api_info()
                else:
                    return ActionResponse(404, {"ok": False, "error": "Not found"})
            body = {"ok": True, "result": result}
            if len(json.dumps(body, ensure_ascii=False).encode("utf-8")) > MAX_RESPONSE_BYTES:
                return ActionResponse(502, {"ok": False, "error": "RTT response was too large"})
            return ActionResponse(200, body)
        except (ValueError, TypeError) as exc:
            return ActionResponse(400, {"ok": False, "error": str(exc)})
        except RTTError as exc:
            return ActionResponse(502, {"ok": False, "error": str(exc)})


def _required(name: str) -> str:
    raise ValueError(f"{name} is required")


def make_handler(application: ActionApplication) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "RTTAction/1.0"

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            parsed = urlsplit(self.path)
            response = application.dispatch(
                "GET", parsed.path.rstrip("/") or "/", parse_qs(parsed.query), self.headers
            )
            if isinstance(response.body, str):
                payload = response.body.encode("utf-8")
            else:
                payload = json.dumps(response.body, ensure_ascii=False).encode("utf-8")
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:
            # Avoid logging query strings, which can contain user-supplied journey details.
            print(f"{self.address_string()} - {format % args}".split("?")[0])

    return Handler


def create_application() -> ActionApplication:
    load_dotenv()
    token = os.environ.get("RTT_TOKEN", "").strip()
    action_key = os.environ.get("ACTION_API_KEY", "").strip()
    if not token:
        raise ValueError("RTT_TOKEN is required in .env or the environment")
    if not action_key:
        raise ValueError("ACTION_API_KEY is required in .env or the environment")
    client = RTTClient(
        token,
        base_url=os.environ.get("RTT_BASE_URL", "https://data.rtt.io"),
        api_version=os.environ.get("RTT_API_VERSION"),
        token_type=os.environ.get("RTT_TOKEN_TYPE", "auto"),
    )
    return ActionApplication(
        RTTRailTools(client),
        api_key=action_key,
        base_url=os.environ.get("ACTION_BASE_URL", "http://127.0.0.1:8765"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the RTT ChatGPT Action API")
    parser.add_argument("--host", default=os.environ.get("ACTION_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("ACTION_PORT", os.environ.get("PORT", "8765")))
    )
    args = parser.parse_args()
    application = create_application()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(application))
    print(f"RTT Action listening on http://{args.host}:{args.port}")
    print(f"OpenAPI schema: {application.base_url}/openapi.json")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
