"""HTTPS-ready, dependency-free HTTP API for a ChatGPT GPT Action."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit

from .cli import load_dotenv
from .client import RTTClient, RTTError
from .movebook_route import MovebookRouteEngine, MovebookRouteError
from .rail_assistant import RTTRailTools
from .tiger import TigerClient, TigerError, reconcile_rtt_tiger, validate_lookup
from .tiger_schema import tiger_operation


MAX_RESPONSE_BYTES = 950_000
MAP_ID_RE = re.compile(r"^[a-f0-9]{24}$")


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
    usage_response = {
        "description": "Aggregate Action API usage statistics",
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "ok": {"type": "boolean"},
                        "result": {
                            "type": "object",
                            "properties": {
                                "trackingSince": {
                                    "type": "string",
                                    "format": "date-time",
                                    "description": "UTC time from which the counters are available.",
                                },
                                "lastRequestAt": {
                                    "type": ["string", "null"],
                                    "format": "date-time",
                                    "description": "UTC time of the most recently counted request.",
                                },
                                "totalRequests": {"type": "integer", "minimum": 0},
                                "requestsByEndpoint": {
                                    "type": "object",
                                    "additionalProperties": {"type": "integer", "minimum": 0},
                                },
                                "responsesByStatus": {
                                    "type": "object",
                                    "additionalProperties": {"type": "integer", "minimum": 0},
                                },
                            },
                            "required": [
                                "trackingSince",
                                "lastRequestAt",
                                "totalRequests",
                                "requestsByEndpoint",
                                "responsesByStatus",
                            ],
                        },
                    },
                    "required": ["ok", "result"],
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
        "503": {
            "description": "Route engine unavailable",
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
            "version": "1.1.0",
        },
        "servers": [{"url": server}],
        "security": [{"bearerAuth": []}, {"actionKey": []}],
        "components": {"securitySchemes": {
            "bearerAuth": {"type": "http", "scheme": "bearer"},
            "actionKey": {"type": "apiKey", "in": "header", "name": "X-Action-Key"},
        }},
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
            "/v1/tiger/service": {"get": tiger_operation(error_responses)},
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
            "/v1/usage": {
                "get": {
                    "operationId": "getApiUsage",
                    "summary": "Get hosted Action API usage",
                    "description": (
                        "Returns aggregate request counts by endpoint and HTTP status. "
                        "No credentials, query values or client identifiers are returned."
                    ),
                    "responses": {"200": usage_response, **error_responses},
                }
            },
            "/v1/route": {
                "get": {
                    "operationId": "suggestRailRoute",
                    "summary": "Suggest a topology-backed railway route and map",
                    "description": (
                        "Uses the Movebook railway routing engine to resolve an origin and "
                        "destination, calculate railway mileage and geometry, and return a map "
                        "link plus valid alternative via points. This is an infrastructure route, "
                        "not a timetable, ticket or guaranteed passenger itinerary."
                    ),
                    "parameters": [
                        {
                            "name": "origin",
                            "in": "query",
                            "required": True,
                            "description": "Origin station name or CRS code.",
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "destination",
                            "in": "query",
                            "required": True,
                            "description": "Destination station name or CRS code.",
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "via",
                            "in": "query",
                            "description": (
                                "Optional comma-separated TIPLOC codes selected from candidates "
                                "returned by an earlier route call, in travel order."
                            ),
                            "schema": {"type": "string"},
                        },
                    ],
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

    def __init__(
        self,
        tools: RTTRailTools,
        *,
        api_key: str,
        base_url: str,
        usage_file: str | None = None,
        route_engine: MovebookRouteEngine | None = None,
        map_dir: str | None = None,
        tiger_client: TigerClient | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("ACTION_API_KEY is required")
        self.tools = tools
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self._lock = threading.RLock()
        self._usage_file = Path(usage_file) if usage_file else None
        self._usage = self._load_usage()
        self.route_engine = route_engine
        self.tiger_client = tiger_client
        self._map_dir = Path(map_dir) if map_dir else None

    def _load_usage(self) -> dict[str, Any]:
        empty = {
            "trackingSince": datetime.now(timezone.utc).isoformat(),
            "lastRequestAt": None,
            "totalRequests": 0,
            "requestsByEndpoint": {},
            "responsesByStatus": {},
        }
        if self._usage_file is None or not self._usage_file.exists():
            return empty
        try:
            loaded = json.loads(self._usage_file.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                return empty
            for key, default in empty.items():
                loaded.setdefault(key, default)
            return loaded
        except (OSError, ValueError, TypeError):
            return empty

    def _save_usage(self) -> None:
        if self._usage_file is None:
            return
        self._usage_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._usage_file.with_suffix(self._usage_file.suffix + ".tmp")
        temporary.write_text(json.dumps(self._usage, sort_keys=True), encoding="utf-8")
        temporary.replace(self._usage_file)

    def record_request(self, path: str, status: int) -> None:
        """Record aggregate /v1 request data without retaining query or client details."""
        if not path.startswith("/v1/"):
            return
        known_paths = {
            "/v1/departures", "/v1/services", "/v1/service", "/v1/info", "/v1/usage", "/v1/route", "/v1/tiger/service"
        }
        path = path if path in known_paths else "/v1/other"
        with self._lock:
            endpoints = self._usage["requestsByEndpoint"]
            statuses = self._usage["responsesByStatus"]
            endpoints[path] = int(endpoints.get(path, 0)) + 1
            status_key = str(status)
            statuses[status_key] = int(statuses.get(status_key, 0)) + 1
            self._usage["totalRequests"] = int(self._usage["totalRequests"]) + 1
            self._usage["lastRequestAt"] = datetime.now(timezone.utc).isoformat()
            try:
                self._save_usage()
            except OSError:
                # Usage reporting must never prevent a rail request from succeeding.
                pass

    def get_usage(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._usage))

    def _save_route_map(self, route: dict[str, Any]) -> str | None:
        if self._map_dir is None:
            return None
        serialised = json.dumps(route, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        map_id = hashlib.sha256(serialised.encode("utf-8")).hexdigest()[:24]
        self._map_dir.mkdir(parents=True, exist_ok=True)
        target = self._map_dir / f"{map_id}.json"
        if not target.exists():
            temporary = target.with_suffix(".json.tmp")
            temporary.write_text(serialised, encoding="utf-8")
            temporary.replace(target)
        return f"{self.base_url}/maps/{map_id}"

    def _route_map(self, map_id: str) -> ActionResponse:
        if self._map_dir is None or not MAP_ID_RE.fullmatch(map_id):
            return ActionResponse(404, {"ok": False, "error": "Map not found"})
        source = self._map_dir / f"{map_id}.json"
        try:
            route = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ActionResponse(404, {"ok": False, "error": "Map not found"})
        return ActionResponse(200, _render_route_map(route), "text/html; charset=utf-8")

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
                "<p>Generated railway maps are stored under unguessable links and contain the "
                "requested origin, destination, route geometry and selected via points. The map "
                "files do not contain credentials or client identifiers.</p>"
            )
            return ActionResponse(200, body, "text/html; charset=utf-8")
        if path.startswith("/maps/"):
            return self._route_map(path.removeprefix("/maps/"))
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
                elif path == "/v1/tiger/service":
                    station = _one(params, "station") or _required("station")
                    uid = _one(params, "uid") or _required("uid")
                    departure_date = _one(params, "departure_date")
                    station = validate_lookup(station, uid, departure_date)
                    if self.tiger_client is None:
                        return ActionResponse(503, {"ok": False, "error": "TIGER is not configured"})
                    result = self.tiger_client.get_service_details(station, uid, departure_date)
                    unique_identity = _one(params, "unique_identity")
                    if unique_identity:
                        rtt = self.tools.get_service_details(unique_identity)
                        result = reconcile_rtt_tiger(rtt, result, station)
                elif path == "/v1/info":
                    result = self.tools.get_api_info()
                elif path == "/v1/usage":
                    result = self.get_usage()
                elif path == "/v1/route":
                    if self.route_engine is None:
                        return ActionResponse(503, {"ok": False, "error": "Route engine unavailable"})
                    via = [code for code in (_one(params, "via", "") or "").split(",") if code.strip()]
                    origin_station = self.tools.resolve_station(
                        _one(params, "origin") or _required("origin")
                    )
                    destination_station = self.tools.resolve_station(
                        _one(params, "destination") or _required("destination")
                    )
                    origin_label = origin_station["name"]
                    destination_label = destination_station["name"]
                    origin_lookup = (
                        origin_label
                        if origin_label.casefold().endswith("rail station")
                        else f"{origin_label} Rail Station"
                    )
                    destination_lookup = (
                        destination_label
                        if destination_label.casefold().endswith("rail station")
                        else f"{destination_label} Rail Station"
                    )
                    result = self.route_engine.route(
                        origin_lookup,
                        destination_lookup,
                        via,
                    )
                    result["origin"] = origin_label
                    result["destination"] = destination_label
                    result["originCode"] = origin_station["code"]
                    result["destinationCode"] = destination_station["code"]
                    result["mapUrl"] = self._save_route_map(result)
                else:
                    return ActionResponse(404, {"ok": False, "error": "Not found"})
            body = {"ok": True, "result": result}
            if len(json.dumps(body, ensure_ascii=False).encode("utf-8")) > MAX_RESPONSE_BYTES:
                return ActionResponse(502, {"ok": False, "error": "RTT response was too large"})
            return ActionResponse(200, body)
        except (ValueError, TypeError) as exc:
            return ActionResponse(400, {"ok": False, "error": str(exc)})
        except TigerError as exc:
            return ActionResponse(exc.status, {"ok": False, "error": str(exc)})
        except RTTError as exc:
            return ActionResponse(502, {"ok": False, "error": str(exc)})
        except MovebookRouteError as exc:
            return ActionResponse(502, {"ok": False, "error": str(exc)})


def _render_route_map(route: Mapping[str, Any]) -> str:
    title = f"{route.get('origin', 'Rail route')} to {route.get('destination', '')}".strip()
    route_json = json.dumps(route, ensure_ascii=False).replace("<", "\\u003c")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline' https://unpkg.com; script-src 'unsafe-inline' https://unpkg.com; img-src data: https://tile.openstreetmap.org https://*.tile.openstreetmap.org">
<title>{html.escape(title)}</title><link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>html,body,#map{{height:100%;margin:0}} .summary{{position:absolute;z-index:1000;left:56px;right:12px;top:12px;max-width:640px;background:#fff;padding:10px 14px;border-radius:8px;box-shadow:0 2px 12px #0004;font:15px system-ui}} .summary strong{{display:block}}</style></head>
<body><div class="summary"><strong>{html.escape(title)}</strong>{html.escape(str(route.get('mileage', '?')))} railway miles · topology-based suggested route</div><div id="map" role="img" aria-label="Interactive map of the suggested railway route"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script><script>
const data={route_json}; const map=L.map('map'); L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{maxZoom:19,attribution:'&copy; OpenStreetMap contributors'}}).addTo(map);
const line=L.polyline(data.coordinates||[],{{color:'#6f42c1',weight:5}}).addTo(map); const esc=s=>String(s).replace(/[&<>\"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}}[c]));
(data.points||[]).forEach(p=>L.circleMarker(p.coordinate,{{radius:p.role==='via'?6:8,color:p.role==='via'?'#6f42c1':'#111',fillOpacity:1}}).addTo(map).bindTooltip(esc(p.label||p.tiploc)));
if(line.getLatLngs().length) map.fitBounds(line.getBounds(),{{padding:[30,30]}}); else map.setView([54.5,-3],6);
</script></body></html>"""


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
            application.record_request(parsed.path.rstrip("/") or "/", response.status)
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
        usage_file=os.environ.get("ACTION_USAGE_FILE"),
        route_engine=(
            MovebookRouteEngine(
                os.environ["MOVEBOOK_ROUTE_SCRIPT"],
                python=os.environ.get("MOVEBOOK_PYTHON", "/usr/bin/python3"),
            )
            if os.environ.get("MOVEBOOK_ROUTE_SCRIPT")
            else None
        ),
        map_dir=os.environ.get("ACTION_MAP_DIR"),
        tiger_client=(
            TigerClient(
                os.environ["TIGER_API_KEY"],
                base_url=os.environ.get("TIGER_BASE_URL", "https://tiger-api-portal.worldline.global"),
                timeout=float(os.environ.get("TIGER_TIMEOUT", "15")),
            ) if os.environ.get("TIGER_API_KEY", "").strip() else None
        ),
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
