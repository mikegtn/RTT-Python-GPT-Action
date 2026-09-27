"""Shared railway service used directly by MCP and by the compatibility HTTP API."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .cli import load_dotenv
from .client import RTTClient, RTTError
from .movebook_route import MovebookRouteEngine, MovebookRouteError
from .map_snapshot import SnapshotError, render as render_snapshot
from .rail_assistant import RTTRailTools
from .journey_route import build_journey_route
from .tiger import TigerClient, TigerError, reconcile_rtt_tiger, validate_lookup, resolve_tiger_tiploc
from .tiger_icons import add_coach_icons


MAX_RESPONSE_BYTES = 950_000
MAP_ID_RE = re.compile(r"^[a-f0-9]{24}$")
OPERATIONS = {
    "/v1/departures": "getNextDepartures", "/v1/services": "searchStationServices",
    "/v1/service": "getServiceDetails", "/v1/info": "getRttApiInfo",
    "/v1/usage": "getApiUsage", "/v1/route": "suggestRailRoute",
    "/v1/journey-route": "getJourneyRoute", "/v1/map-snapshot": "getRailMapSnapshot",
    "/v1/tiger/service": "getTigerServiceDetails",
}


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


@dataclass(frozen=True)
class RailResponse:
    status: int
    body: Any
    content_type: str = "application/json; charset=utf-8"


def add_evidence(path, response):
    if path in OPERATIONS and isinstance(response.body, dict):
        evidence = {"requestId": secrets.token_hex(12), "operation": OPERATIONS[path],
                    "completedAt": datetime.now(timezone.utc).isoformat()}
        return RailResponse(response.status, {"requestEvidence": evidence, **response.body}, response.content_type)
    return response


class RailwayService:
    """Shared railway operations, evidence, usage and artifacts; no HTTP transport."""

    def __init__(
        self,
        tools: RTTRailTools,
        *,
        base_url: str,
        usage_file: str | None = None,
        route_engine: MovebookRouteEngine | None = None,
        map_dir: str | None = None,
        tiger_client: TigerClient | None = None,
    ) -> None:
        self.tools = tools
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
            *OPERATIONS
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

    def _route_map(self, map_id: str) -> RailResponse:
        is_image = map_id.endswith(".png")
        if is_image:
            map_id = map_id[:-4]
        if self._map_dir is None or not MAP_ID_RE.fullmatch(map_id):
            return RailResponse(404, {"ok": False, "error": "Map not found"})
        if is_image:
            try:
                return RailResponse(200, (self._map_dir / f"{map_id}.png").read_bytes(), "image/png")
            except OSError:
                return RailResponse(404, {"ok": False, "error": "Map image not found"})
        source = self._map_dir / f"{map_id}.json"
        try:
            route = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return RailResponse(404, {"ok": False, "error": "Map not found"})
        return RailResponse(200, _render_route_map(route), "text/html; charset=utf-8")

    def _snapshot(self, map_id: str) -> RailResponse:
        if self._map_dir is None or not MAP_ID_RE.fullmatch(map_id):
            return RailResponse(404, {"ok": False, "error": "Map not found"})
        try:
            route = json.loads((self._map_dir / f"{map_id}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return RailResponse(404, {"ok": False, "error": "Map not found"})
        try:
            render_snapshot(route, self._map_dir / f"{map_id}.png")
        except SnapshotError as exc:
            return RailResponse(503, {"ok": False, "error": str(exc)})
        return RailResponse(200, {"ok": True, "result": {
            "mapUrl": f"{self.base_url}/maps/{map_id}",
            "mapImageUrl": f"{self.base_url}/maps/{map_id}.png",
            "imageAlt": f"Railway route from {route.get('origin', 'origin')} to {route.get('destination', 'destination')}",
        }})

    def execute(self, path, params):
        """Run an operation directly, retaining the existing evidence contract."""
        with self._lock:
            response = add_evidence(path, self._execute(path, params))
            self.record_request(path, response.status)
            return response

    def _execute(self, path, params):
        try:
            if path == "/v1/map-snapshot":
                return self._snapshot(_one(params, "map_id") or _required("map_id"))
            include_snapshot = _boolean(params, "include_snapshot") if path in {"/v1/route", "/v1/journey-route"} else False
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
                        return RailResponse(503, {"ok": False, "error": "TIGER is not configured"})
                    unique_identity = _one(params, "unique_identity")
                    rtt = self.tools.get_service_details(unique_identity) if unique_identity else None
                    tiploc = resolve_tiger_tiploc(station, rtt, _one(params, "tiploc"))
                    result = add_coach_icons(
                        self.tiger_client.get_service_details(tiploc, uid, departure_date), self.base_url
                    )
                    if rtt is not None:
                        result = reconcile_rtt_tiger(rtt, result, tiploc, requested_station=station)
                elif path == "/v1/info":
                    result = self.tools.get_api_info()
                elif path == "/v1/usage":
                    result = self.get_usage()
                elif path == "/v1/journey-route":
                    if self.route_engine is None:
                        return RailResponse(503, {"ok": False, "error": "Route engine unavailable"})
                    result = build_journey_route(self.tools, self.route_engine,
                                                 _one(params, "legs") or _required("legs"))
                    result["mapUrl"] = self._save_route_map(result)
                    # The browser map needs geometry; the model needs the evidence
                    # and URL. Avoid flooding its retained tool context with vertices.
                    result["schedulePointCount"] = len(result.get("requested_route_guidance", []))
                    result = {key: value for key, value in result.items() if key in {
                        "origin", "destination", "mileage", "routeBasis", "legs",
                        "minimumConnectionTimesVerified", "routing_warnings", "mapUrl",
                        "schedulePointCount", "attribution",
                    }}
                elif path == "/v1/route":
                    if self.route_engine is None:
                        return RailResponse(503, {"ok": False, "error": "Route engine unavailable"})
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
                    return RailResponse(404, {"ok": False, "error": "Not found"})
            if include_snapshot:
                if result.get("mapUrl"):
                    snapshot = self._snapshot(result["mapUrl"].rsplit("/", 1)[-1])
                    if snapshot.status == 200:
                        result.update(snapshot.body["result"])
                    else:
                        result["snapshotError"] = snapshot.body["error"]
                else:
                    result["snapshotError"] = "Map snapshots are not configured on this server"
            if path in {"/v1/route", "/v1/journey-route"} and result.get("mapUrl"):
                result["interactiveMapMarkdown"] = f"[Interactive route map]({result['mapUrl']})"
                if result.get("mapImageUrl"):
                    result["snapshotMarkdown"] = f"[View snapshot]({result['mapImageUrl']})"
            body = {"ok": True, "result": result}
            if len(json.dumps(body, ensure_ascii=False).encode("utf-8")) > MAX_RESPONSE_BYTES:
                return RailResponse(502, {"ok": False, "error": "RTT response was too large"})
            return RailResponse(200, body)
        except (ValueError, TypeError) as exc:
            return RailResponse(400, {"ok": False, "error": str(exc)})
        except TigerError as exc:
            return RailResponse(exc.status, {"ok": False, "error": str(exc)})
        except RTTError as exc:
            return RailResponse(502, {"ok": False, "error": str(exc)})
        except MovebookRouteError as exc:
            return RailResponse(502, {"ok": False, "error": str(exc)})


def _render_route_map(route: Mapping[str, Any]) -> str:
    title = f"{route.get('origin', 'Rail route')} to {route.get('destination', '')}".strip()
    basis = route.get("routeBasis", "topology-based suggested route")
    route_json = json.dumps(route, ensure_ascii=False).replace("<", "\\u003c")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="strict-origin-when-cross-origin">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline' https://unpkg.com; script-src 'unsafe-inline' https://unpkg.com; img-src data: https://tile.openstreetmap.org https://*.tile.openstreetmap.org">
<title>{html.escape(title)}</title><link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>html,body,#map{{height:100%;margin:0}} .summary{{position:absolute;z-index:1000;left:56px;right:12px;top:12px;max-width:640px;background:#fff;padding:10px 14px;border-radius:8px;box-shadow:0 2px 12px #0004;font:15px system-ui}} .summary strong{{display:block}}</style></head>
<body><div class="summary"><strong>{html.escape(title)}</strong>{html.escape(str(route.get('mileage', '?')))} railway miles · {html.escape(str(basis))}</div><div id="map" role="img" aria-label="Interactive map of the suggested railway route"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script><script>
const data={route_json}; const map=L.map('map'); L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{maxZoom:19,attribution:'&copy; OpenStreetMap contributors'}}).addTo(map);
const line=L.polyline(data.coordinates||[],{{color:'#6f42c1',weight:5}}).addTo(map); const esc=s=>String(s).replace(/[&<>\"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}}[c]));
(data.points||[]).forEach(p=>L.circleMarker(p.coordinate,{{radius:p.role==='via'?6:8,color:p.role==='via'?'#6f42c1':'#111',fillOpacity:1}}).addTo(map).bindTooltip(esc(p.label||p.tiploc)));
if(line.getLatLngs().length) map.fitBounds(line.getBounds(),{{padding:[30,30]}}); else map.setView([54.5,-3],6);
</script></body></html>"""


def _required(name: str) -> str:
    raise ValueError(f"{name} is required")


def create_railway_service(*, base_url=None, usage_file=None, map_dir=None) -> RailwayService:
    load_dotenv()
    token = os.environ.get("RTT_TOKEN", "").strip()
    if not token:
        raise ValueError("RTT_TOKEN is required in .env or the environment")
    client = RTTClient(
        token,
        base_url=os.environ.get("RTT_BASE_URL", "https://data.rtt.io"),
        api_version=os.environ.get("RTT_API_VERSION"),
        token_type=os.environ.get("RTT_TOKEN_TYPE", "auto"),
    )
    return RailwayService(
        RTTRailTools(client),
        base_url=base_url or os.environ.get("ACTION_BASE_URL", "http://127.0.0.1:8765"),
        usage_file=usage_file if usage_file is not None else os.environ.get("ACTION_USAGE_FILE"),
        route_engine=(
            MovebookRouteEngine(
                os.environ["MOVEBOOK_ROUTE_SCRIPT"],
                python=os.environ.get("MOVEBOOK_PYTHON", "/usr/bin/python3"),
            )
            if os.environ.get("MOVEBOOK_ROUTE_SCRIPT")
            else None
        ),
        map_dir=map_dir if map_dir is not None else os.environ.get("ACTION_MAP_DIR"),
        tiger_client=(
            TigerClient(
                os.environ["TIGER_API_KEY"],
                base_url=os.environ.get("TIGER_BASE_URL", "https://tiger-api-portal.worldline.global"),
                timeout=float(os.environ.get("TIGER_TIMEOUT", "15")),
            ) if os.environ.get("TIGER_API_KEY", "").strip() else None
        ),
    )

