"""MCP tool contracts and bounded workflows over the existing Action API.

Opaque RTT service identities are preserved exactly. Backend request evidence may
be used for server-side logging but is not part of the public MCP result contract.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import secrets
from typing import Any
from zoneinfo import ZoneInfo

from .action_api import build_openapi_schema


def object_schema(properties, required=(), *, additional=False):
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": additional,
    }


def nullable(schema):
    return {"anyOf": [deepcopy(schema), {"type": "null"}]}


JSON_OBJECT = {"type": "object", "additionalProperties": True}
JSON_VALUE = {}
STRING = {"type": "string"}
STRING_OR_NULL = {"type": ["string", "null"]}
STRING_ARRAY = {"type": "array", "items": STRING}
URI_OR_NULL = {"type": ["string", "null"], "format": "uri"}

IDENTITY = {
    "type": "string",
    "minLength": 1,
    "maxLength": 200,
    "description": "Exact uniqueIdentity returned by RTT; never construct it.",
}
STATION = {"type": "string", "minLength": 1, "maxLength": 100}
LEG = object_schema(
    {"unique_identity": IDENTITY, "origin": STATION, "destination": STATION},
    ["unique_identity", "origin", "destination"],
)

PAIR = object_schema(
    {
        "description": STRING_OR_NULL,
        "shortCodes": nullable({"type": "array", "items": STRING}),
        "longCodes": nullable({"type": "array", "items": STRING}),
        "temporalData": nullable(JSON_OBJECT),
    },
    ["description", "shortCodes", "longCodes", "temporalData"],
)

DEPARTURE = object_schema(
    {
        "scheduled": STRING,
        "expected": STRING,
        "destination": STRING,
        "allocation": STRING,
        "status": STRING,
        "platform": STRING,
    },
    ["scheduled", "expected", "destination", "allocation", "status", "platform"],
)

SERVICE_SUMMARY = object_schema(
    {
        "scheduleMetadata": nullable(JSON_OBJECT),
        "temporalData": nullable(JSON_OBJECT),
        "locationMetadata": nullable(JSON_OBJECT),
        "origin": {"type": "array", "items": PAIR},
        "destination": {"type": "array", "items": PAIR},
        "reasons": nullable({"type": "array", "items": JSON_OBJECT}),
    },
    ["scheduleMetadata", "temporalData", "locationMetadata", "origin", "destination", "reasons"],
)

CALL = object_schema(
    {
        "location": nullable(JSON_OBJECT),
        "temporalData": JSON_OBJECT,
        "locationMetadata": nullable(JSON_OBJECT),
        "associatedServices": nullable({"type": "array", "items": JSON_OBJECT}),
    },
    ["location", "temporalData", "locationMetadata", "associatedServices"],
)

SERVICE_DETAILS = object_schema(
    {
        "scheduleMetadata": nullable(JSON_OBJECT),
        "origin": {"type": "array", "items": PAIR},
        "destination": {"type": "array", "items": PAIR},
        "allocationData": JSON_VALUE,
        "reasons": nullable({"type": "array", "items": JSON_OBJECT}),
        "calls": {"type": "array", "items": CALL},
    },
    ["scheduleMetadata", "origin", "destination", "allocationData", "reasons", "calls"],
)

COORDINATE = {
    "anyOf": [
        {
            "type": "array",
            "prefixItems": [{"type": "number"}, {"type": "number"}],
            "minItems": 2,
            "maxItems": 2,
        },
        {"type": "null"},
    ]
}

ROUTE_CANDIDATE = object_schema(
    {"tiploc": STRING, "label": STRING_OR_NULL, "coordinate": COORDINATE},
    ["tiploc", "label", "coordinate"],
)

ROUTE_RESULT = object_schema(
    {
        "origin": STRING,
        "destination": STRING,
        "originCode": STRING,
        "destinationCode": STRING,
        "mileage": {"type": "number"},
        "routeBasis": STRING,
        "candidates": {"type": "array", "items": ROUTE_CANDIDATE},
        "mapUrl": URI_OR_NULL,
        "mapImageUrl": {"type": "string", "format": "uri"},
        "imageAlt": STRING,
        "snapshotError": STRING,
        "interactiveMapMarkdown": STRING,
        "snapshotMarkdown": STRING,
        "attribution": JSON_VALUE,
    },
    additional=True,
)

JOURNEY_ROUTE_RESULT = object_schema(
    {
        "origin": STRING,
        "destination": STRING,
        "mileage": {"type": "number"},
        "routeBasis": STRING,
        "legs": {"type": "array", "items": JSON_OBJECT},
        "minimumConnectionTimesVerified": {"type": "boolean"},
        "routing_warnings": {"type": "array", "items": STRING},
        "mapUrl": URI_OR_NULL,
        "schedulePointCount": {"type": "integer", "minimum": 0},
        "attribution": JSON_VALUE,
        "mapImageUrl": {"type": "string", "format": "uri"},
        "imageAlt": STRING,
        "snapshotError": STRING,
        "interactiveMapMarkdown": STRING,
        "snapshotMarkdown": STRING,
    },
    additional=False,
)

MAP_SNAPSHOT_RESULT = object_schema(
    {
        "mapUrl": {"type": "string", "format": "uri"},
        "mapImageUrl": {"type": "string", "format": "uri"},
        "imageAlt": STRING,
    },
    ["mapUrl", "mapImageUrl", "imageAlt"],
)

API_INFO_RESULT = {
    "type": "object",
    "properties": {
        "version": STRING,
        "api_version": STRING,
        "entitlements": JSON_VALUE,
        "historyRestriction": JSON_VALUE,
        "historyRestrictToDays": JSON_VALUE,
        "namespaceRestriction": JSON_VALUE,
    },
    "additionalProperties": True,
}

USAGE_RESULT = object_schema(
    {
        "trackingSince": {"type": "string", "format": "date-time"},
        "lastRequestAt": {"type": ["string", "null"], "format": "date-time"},
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
    ["trackingSince", "lastRequestAt", "totalRequests", "requestsByEndpoint", "responsesByStatus"],
)

PASSENGER_LEG = object_schema(
    {
        "uniqueIdentity": IDENTITY,
        "origin": STATION,
        "destination": STATION,
        "departure": JSON_OBJECT,
        "arrival": JSON_OBJECT,
        "originLocationMetadata": JSON_VALUE,
        "destinationLocationMetadata": JSON_VALUE,
        "allocationData": JSON_VALUE,
    },
    [
        "uniqueIdentity", "origin", "destination", "departure", "arrival",
        "originLocationMetadata", "destinationLocationMetadata", "allocationData",
    ],
)

ITINERARY = object_schema(
    {
        "legs": {"type": "array", "minItems": 1, "maxItems": 2, "items": PASSENGER_LEG},
        "connectionMinutes": {"type": "number"},
        "warnings": STRING_ARRAY,
    },
    ["legs", "warnings"],
)

COVERAGE = object_schema(
    {
        "origin": STATION,
        "destination": STATION,
        "timeFrom": {"type": "string", "format": "date-time"},
        "minutes": {"type": "integer", "minimum": 1},
        "candidatesReturned": {"type": "integer", "minimum": 0},
        "candidateLimit": {"type": "integer", "minimum": 1},
        "possiblyTruncated": {"type": "boolean"},
    },
    [
        "origin", "destination", "timeFrom", "minutes", "candidatesReturned",
        "candidateLimit", "possiblyTruncated",
    ],
)

FIND_JOURNEYS_RESULT = object_schema(
    {
        "origin": STATION,
        "destination": STATION,
        "timeFrom": {"type": "string", "format": "date-time"},
        "timeTo": {"type": "string", "format": "date-time"},
        "itineraries": {"type": "array", "maxItems": 3, "items": ITINERARY},
        "matchedOptions": {"type": "integer", "minimum": 0},
        "coverage": {"type": "array", "items": COVERAGE},
        "connectionBufferMinutes": {"type": "integer", "minimum": 1},
        "timeZone": STRING,
        "minimumConnectionTimesVerified": {"type": "boolean"},
        "coverageLimit": STRING,
    },
    [
        "origin", "destination", "timeFrom", "timeTo", "itineraries",
        "matchedOptions", "coverage", "connectionBufferMinutes", "timeZone",
        "minimumConnectionTimesVerified", "coverageLimit",
    ],
)

LAST_REPORT = object_schema(
    {
        "location": nullable(JSON_OBJECT),
        "event": {"type": "string", "enum": ["arrival", "pass", "departure"]},
        "reportedAt": {"type": "string"},
        "timing": JSON_OBJECT,
    },
    ["location", "event", "reportedAt", "timing"],
)

TRAIN_LOCATION_RESULT = object_schema(
    {
        "uniqueIdentity": IDENTITY,
        "lastReport": nullable(LAST_REPORT),
        "retrievedAt": {"type": "string", "format": "date-time"},
        "positionBasis": STRING,
        "timeZone": STRING,
        "reportAgeSeconds": {"type": ["integer", "null"], "minimum": 0},
        "warnings": STRING_ARRAY,
    },
    [
        "uniqueIdentity", "lastReport", "retrievedAt", "positionBasis",
        "timeZone", "reportAgeSeconds", "warnings",
    ],
)

ROUTE_DETAILS_RESULT = object_schema(
    {
        "uniqueIdentity": IDENTITY,
        "scheduleMetadata": nullable(JSON_OBJECT),
        "calls": {"type": "array", "items": CALL},
        "origin": {"type": "array", "items": PAIR},
        "destination": {"type": "array", "items": PAIR},
        "reasons": nullable({"type": "array", "items": JSON_OBJECT}),
        "routeBasis": STRING,
    },
    ["uniqueIdentity", "scheduleMetadata", "calls", "origin", "destination", "reasons", "routeBasis"],
)

RESULT_SCHEMAS = {
    "getNextDepartures": object_schema(
        {
            "station": STRING,
            "stationCode": STRING,
            "departures": {"type": "array", "items": DEPARTURE},
        },
        ["station", "stationCode", "departures"],
    ),
    "searchStationServices": object_schema(
        {
            "query": JSON_VALUE,
            "systemStatus": JSON_VALUE,
            "services": {"type": "array", "items": SERVICE_SUMMARY},
        },
        ["query", "systemStatus", "services"],
    ),
    "getServiceDetails": SERVICE_DETAILS,
    "getRttApiInfo": API_INFO_RESULT,
    "getApiUsage": USAGE_RESULT,
    "suggestRailRoute": ROUTE_RESULT,
    "getJourneyRoute": JOURNEY_ROUTE_RESULT,
    "getRailMapSnapshot": MAP_SNAPSHOT_RESULT,
    "findJourneys": FIND_JOURNEYS_RESULT,
    "getTrainLocation": TRAIN_LOCATION_RESULT,
    "getRouteDetails": ROUTE_DETAILS_RESULT,
}


def _operation_result_schema(operation):
    """Reuse a detailed Action schema when that endpoint already defines one."""
    try:
        response = operation["responses"]["200"]["content"]["application/json"]["schema"]
        result = response["properties"]["result"]
    except (KeyError, TypeError):
        return None
    return deepcopy(result)


def tool_catalog():
    catalog = {}
    for path, methods in build_openapi_schema("https://rail.mikegtn.net")["paths"].items():
        operation = methods["get"]
        properties, required = {}, []
        for parameter in operation.get("parameters", []):
            definition = deepcopy(parameter["schema"])
            if parameter.get("description"):
                definition["description"] = parameter["description"]
            if definition.get("type") == "string":
                definition.setdefault("maxLength", 8000 if parameter["name"] == "legs" else 200)
            properties[parameter["name"]] = definition
            if parameter.get("required"):
                required.append(parameter["name"])
        if operation["operationId"] == "getJourneyRoute":
            properties["legs"] = {"type": "array", "minItems": 1, "maxItems": 6, "items": LEG}
        creates_map = path in {"/v1/route", "/v1/journey-route", "/v1/map-snapshot"}
        result_schema = RESULT_SCHEMAS.get(operation["operationId"]) or _operation_result_schema(operation)
        if result_schema is None:
            raise ValueError(f"No public output schema for {operation['operationId']}")
        catalog[operation["operationId"]] = {
            "name": operation["operationId"],
            "title": operation["summary"],
            "description": operation["description"],
            "inputSchema": object_schema(properties, required),
            "outputSchema": result_schema,
            "annotations": {
                "readOnlyHint": not creates_map,
                "destructiveHint": False,
                "idempotentHint": not creates_map,
                "openWorldHint": True,
            },
            "path": path,
        }

    additions = [
        (
            "findJourneys",
            "Find dated passenger journeys",
            "Search direct trains and up to three supplied interchange stations. Inspect exact RTT services, "
            "advertised times, restrictions and cancellations. Bounded search, not a complete journey planner; "
            "minimum interchange times are not verified. Returns up to three options and coverage limits.",
            object_schema(
                {
                    "origin": STATION,
                    "destination": STATION,
                    "time_from": {
                        "type": "string",
                        "format": "date-time",
                        "description": "Explicit ISO datetime with UTC offset.",
                    },
                    "minutes": {"type": "integer", "minimum": 1, "maximum": 1439, "default": 720},
                    "interchanges": {
                        "type": "array",
                        "maxItems": 3,
                        "uniqueItems": True,
                        "items": STATION,
                        "default": [],
                    },
                    "connection_minutes": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 180,
                        "default": 15,
                        "description": "Search buffer, not a verified minimum interchange time.",
                    },
                },
                ["origin", "destination", "time_from"],
            ),
        ),
        (
            "getTrainLocation",
            "Get the last reported train location",
            "Return the latest actual RTT timing report for an exact dated service. "
            "This is a last report, not GPS or a claim about its present position. Forecasts are not observations.",
            object_schema({"unique_identity": IDENTITY}, ["unique_identity"]),
        ),
        (
            "getRouteDetails",
            "Get a dated service's route details",
            "Return ordered RTT schedule calls and timing points available from service details, "
            "including restrictions and live timings. Does not generate geometry or establish mileage.",
            object_schema({"unique_identity": IDENTITY}, ["unique_identity"]),
        ),
    ]
    for name, title, description, schema in additions:
        catalog[name] = {
            "name": name,
            "title": title,
            "description": description,
            "inputSchema": schema,
            "outputSchema": RESULT_SCHEMAS[name],
            "annotations": {
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
            },
        }
    return catalog


def timestamp(value, *, require_offset=False):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        if require_offset:
            raise ValueError("Query times must include a UTC offset")
        # RTT gb-nr timing fields are UK local wall-clock values. Preserve the
        # original strings in responses; normalize only for comparisons.
        local = result.replace(tzinfo=ZoneInfo("Europe/London"))
        if local.utcoffset() != local.replace(fold=1).utcoffset():
            raise ValueError("An RTT time is ambiguous or nonexistent at the UK clock change")
        result = local
    return result.astimezone(timezone.utc)


def matches(call, station):
    location = call.get("location") or {}
    values = [location.get("description", ""), *(location.get("shortCodes") or []),
              *(location.get("longCodes") or [])]
    normalize = lambda s: str(s).casefold().removesuffix(" rail station").strip()
    return normalize(station) in [normalize(v) for v in values]


def passenger_leg(service, identity, origin, destination):
    metadata = service.get("scheduleMetadata") or {}
    if metadata.get("uniqueIdentity") != identity:
        raise ValueError("RTT returned a different service identity")
    if metadata.get("inPassengerService") is not True:
        return None
    calls = service.get("calls") or []
    starts = [i for i, c in enumerate(calls) if matches(c, origin)]
    ends = [i for i, c in enumerate(calls) if matches(c, destination)]
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        return None
    section = calls[starts[0]:ends[0] + 1]
    dep, arr = section[0].get("temporalData") or {}, section[-1].get("temporalData") or {}
    if dep.get("scheduledCallType") not in {"ADVERTISED_OPEN", "ADVERTISED_PICK_UP"}:
        return None
    if arr.get("scheduledCallType") not in {"ADVERTISED_OPEN", "ADVERTISED_SET_DOWN"}:
        return None
    for call in section:
        temporal = call.get("temporalData") or {}
        if temporal.get("displayAs") in {"CANCELLED", "DIVERTED"} or str(temporal.get("realtimeCallType", "")).startswith("CANCELLED"):
            return None
        if any(isinstance(v, dict) and v.get("isCancelled") is True for v in temporal.values()):
            return None
    if any(r.get("type") == "CANCEL" for r in service.get("reasons") or []):
        return None
    departure, arrival = dep.get("departure") or {}, arr.get("arrival") or {}
    # Passenger plans require advertised times; technical times are not substitutes.
    if not departure.get("scheduleAdvertised") or not arrival.get("scheduleAdvertised"):
        return None
    if timestamp(arrival["scheduleAdvertised"]) <= timestamp(departure["scheduleAdvertised"]):
        return None
    return {"uniqueIdentity": identity, "origin": origin, "destination": destination,
            "departure": departure, "arrival": arrival,
            "originLocationMetadata": section[0].get("locationMetadata"),
            "destinationLocationMetadata": section[-1].get("locationMetadata"),
            "allocationData": service.get("allocationData")}


class RailWorkflows:
    def __init__(self, backend):
        self.backend = backend
        self.catalog = tool_catalog()

    async def call(self, name: str, arguments: dict[str, Any]):
        if name not in self.catalog:
            raise ValueError("Unknown tool")
        if "path" in self.catalog[name]:
            params = dict(arguments)
            if name == "getJourneyRoute":
                params["legs"] = json.dumps(params["legs"], separators=(",", ":"))
            return await self.backend(self.catalog[name]["path"], params)
        evidence = []

        async def request(path, params):
            body = await self.backend(path, params)
            if body.get("requestEvidence"):
                evidence.append(body["requestEvidence"])
            if not body.get("ok"):
                raise ValueError(body.get("error", "RTT request failed"))
            return body["result"]

        try:
            if name == "findJourneys":
                result = await self.find_journeys(request, **arguments)
            else:
                identity = arguments["unique_identity"]
                service = await request("/v1/service", {"unique_identity": identity})
                if (service.get("scheduleMetadata") or {}).get("uniqueIdentity") != identity:
                    raise ValueError("RTT returned a different service identity")
                if name == "getRouteDetails":
                    result = {"uniqueIdentity": identity, "scheduleMetadata": service.get("scheduleMetadata"),
                              "calls": service.get("calls", []), "origin": service.get("origin"),
                              "destination": service.get("destination"), "reasons": service.get("reasons"),
                              "routeBasis": "Ordered RTT service-detail timing points; no geometry or mileage inferred"}
                else:
                    now = datetime.now(timezone.utc)
                    reports = []
                    for call in service.get("calls") or []:
                        if (call.get("temporalData") or {}).get("isInterpolated") is True:
                            continue
                        for event in ("arrival", "pass", "departure"):
                            data = (call.get("temporalData") or {}).get(event) or {}
                            actual = data.get("realtimeActual")
                            if actual and not data.get("isCancelled") and timestamp(actual) <= now:
                                reports.append({"location": call.get("location"), "event": event,
                                                "reportedAt": actual, "timing": data})
                    report = max(reports, key=lambda r: timestamp(r["reportedAt"])) if reports else None
                    result = {"uniqueIdentity": identity, "lastReport": report,
                              "retrievedAt": now.isoformat(), "positionBasis": "Last actual RTT timing report; not GPS",
                              "timeZone": "Europe/London for RTT timestamps without an explicit offset",
                              "reportAgeSeconds": max(0, int((now - timestamp(report["reportedAt"])).total_seconds())) if report else None,
                              "warnings": ["The train may have moved since this report."] if report else ["No actual location report is available."]}
            body = {"ok": True, "result": result}
        except ValueError as exc:
            body = {"ok": False, "error": str(exc)}
        body["requestEvidence"] = {"requestId": secrets.token_hex(12), "operation": name,
                                   "completedAt": datetime.now(timezone.utc).isoformat()}
        body["sourceRequestEvidence"] = evidence
        return body

    async def find_journeys(self, request, origin, destination, time_from, minutes=720,
                            interchanges=None, connection_minutes=15):
        start = timestamp(time_from, require_offset=True)
        end = start + timedelta(minutes=minutes)
        interchanges = interchanges or []
        cache, coverage, options = {}, [], []
        # Six candidates per board bounds upstream work. Every limit is disclosed.
        async def legs(a, b, begin, window):
            board = await request("/v1/services", {"station": a, "filter_to": b,
                                  "time_from": begin.isoformat(), "minutes": window,
                                  "movement": "departures", "count": 6})
            items = board.get("services") or []
            coverage.append({"origin": a, "destination": b, "timeFrom": begin.isoformat(),
                             "minutes": window, "candidatesReturned": len(items),
                             "candidateLimit": 6, "possiblyTruncated": len(items) >= 6})
            result = []
            for item in items:
                identity = (item.get("scheduleMetadata") or {}).get("uniqueIdentity")
                if not identity:
                    continue
                if identity not in cache:
                    cache[identity] = await request("/v1/service", {"unique_identity": identity})
                leg = passenger_leg(cache[identity], identity, a, b)
                if leg and begin <= timestamp(leg["departure"]["scheduleAdvertised"]) < begin + timedelta(minutes=window):
                    result.append(leg)
            return result

        for leg in await legs(origin, destination, start, minutes):
            options.append({"legs": [leg], "warnings": []})
        for interchange in interchanges:
            first = await legs(origin, interchange, start, minutes)
            if not first:
                continue
            earliest = min(timestamp(l["arrival"]["scheduleAdvertised"]) for l in first)
            onward = await legs(interchange, destination, earliest, 1439)
            for a in first:
                for b in onward:
                    if a["uniqueIdentity"] == b["uniqueIdentity"]:
                        continue
                    gap = (timestamp(b["departure"]["scheduleAdvertised"]) - timestamp(a["arrival"]["scheduleAdvertised"])).total_seconds() / 60
                    if not connection_minutes <= gap <= 240:
                        continue
                    warnings = ["Minimum interchange time has not been verified; the buffer is a search assumption."]
                    latest_a = a["arrival"].get("realtimeActual") or a["arrival"].get("realtimeForecast")
                    latest_b = b["departure"].get("realtimeActual") or b["departure"].get("realtimeForecast")
                    if latest_a and latest_b and (timestamp(latest_b) - timestamp(latest_a)).total_seconds() < connection_minutes * 60:
                        warnings.append("Latest running times do not allow the assumed connection buffer.")
                    options.append({"legs": [a, b], "connectionMinutes": gap, "warnings": warnings})
        options.sort(key=lambda o: timestamp(o["legs"][-1]["arrival"]["scheduleAdvertised"]))
        return {"origin": origin, "destination": destination, "timeFrom": start.isoformat(),
                "timeTo": end.isoformat(), "itineraries": options[:3], "matchedOptions": len(options),
                "coverage": coverage, "connectionBufferMinutes": connection_minutes,
                "timeZone": "Europe/London for RTT timestamps without an explicit offset",
                "minimumConnectionTimesVerified": False,
                "coverageLimit": "Direct and supplied single-interchange routes only; six candidates per board, "
                                 "onward search under 24 hours, waits at most four hours. Not exhaustive; "
                                 "no claim of fastest, earliest or only service. Live data can change."}
