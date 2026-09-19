"""MCP tool contracts and bounded workflows over the existing Action API.

The Action remains the authority: opaque identities and request evidence are
returned unchanged. No upstream credentials are exposed to a model.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import secrets
from typing import Any

from .action_api import build_openapi_schema


def object_schema(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required),
            "additionalProperties": False}


IDENTITY = {"type": "string", "minLength": 1, "maxLength": 200,
            "description": "Exact uniqueIdentity returned by RTT; never construct it."}
STATION = {"type": "string", "minLength": 1, "maxLength": 100}
LEG = object_schema({"unique_identity": IDENTITY, "origin": STATION, "destination": STATION},
                    ["unique_identity", "origin", "destination"])


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
        catalog[operation["operationId"]] = {
            "name": operation["operationId"], "title": operation["summary"],
            "description": operation["description"], "inputSchema": object_schema(properties, required),
            "outputSchema": {"type": "object", "properties": {"ok": {"type": "boolean"}},
                             "required": ["ok"], "additionalProperties": True},
            "annotations": {"readOnlyHint": not creates_map, "destructiveHint": False,
                            "idempotentHint": not creates_map, "openWorldHint": True},
            "path": path,
        }
    additions = [
        ("findJourneys", "Find dated passenger journeys",
         "Search direct trains and up to three supplied interchange stations. Inspect exact RTT services, "
         "advertised times, restrictions and cancellations. Bounded search, not a complete journey planner; "
         "minimum interchange times are not verified. Returns up to three options and coverage limits.",
         object_schema({"origin": STATION, "destination": STATION,
                        "time_from": {"type": "string", "format": "date-time",
                                      "description": "Explicit ISO datetime with UTC offset."},
                        "minutes": {"type": "integer", "minimum": 1, "maximum": 1439, "default": 720},
                        "interchanges": {"type": "array", "maxItems": 3, "uniqueItems": True,
                                         "items": STATION, "default": []},
                        "connection_minutes": {"type": "integer", "minimum": 1, "maximum": 180,
                                               "default": 15,
                                               "description": "Search buffer, not a verified minimum interchange time."}},
                       ["origin", "destination", "time_from"])),
        ("getTrainLocation", "Get the last reported train location",
         "Return the latest actual RTT timing report for an exact dated service. "
         "This is a last report, not GPS or a claim about its present position. Forecasts are not observations.",
         object_schema({"unique_identity": IDENTITY}, ["unique_identity"])),
        ("getRouteDetails", "Get a dated service's route details",
         "Return ordered RTT schedule calls and timing points available from service details, "
         "including restrictions and live timings. Does not generate geometry or establish mileage.",
         object_schema({"unique_identity": IDENTITY}, ["unique_identity"])),
    ]
    for name, title, description, schema in additions:
        catalog[name] = {"name": name, "title": title, "description": description, "inputSchema": schema,
                         "outputSchema": {"type": "object", "required": ["ok"],
                                          "properties": {"ok": {"type": "boolean"}}, "additionalProperties": True},
                         "annotations": {"readOnlyHint": True, "destructiveHint": False,
                                         "idempotentHint": True, "openWorldHint": True}}
    return catalog


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Times must include a UTC offset")
    return result


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
                        for event in ("arrival", "pass", "departure"):
                            data = (call.get("temporalData") or {}).get(event) or {}
                            actual = data.get("realtimeActual")
                            if actual and not data.get("isCancelled") and timestamp(actual) <= now:
                                reports.append({"location": call.get("location"), "event": event,
                                                "reportedAt": actual, "timing": data})
                    report = max(reports, key=lambda r: timestamp(r["reportedAt"])) if reports else None
                    result = {"uniqueIdentity": identity, "lastReport": report,
                              "retrievedAt": now.isoformat(), "positionBasis": "Last actual RTT timing report; not GPS",
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
        start = timestamp(time_from)
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
                "minimumConnectionTimesVerified": False,
                "coverageLimit": "Direct and supplied single-interchange routes only; six candidates per board, "
                                 "onward search under 24 hours, waits at most four hours. Not exhaustive; "
                                 "no claim of fastest, earliest or only service. Live data can change."}
