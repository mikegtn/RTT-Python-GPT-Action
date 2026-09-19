"""Build one map from dated RTT legs; never replace them with a generic route."""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any


def _code(point: dict[str, Any]) -> str:
    location = point.get("location") or {}
    codes = location.get("longCodes") or location.get("shortCodes") or []
    codes = list(dict.fromkeys(c for c in codes if isinstance(c, str) and re.fullmatch(r"[A-Z0-9]{2,7}", c)))
    if len(codes) != 1:
        raise ValueError("A schedule point has missing or ambiguous railway codes; no itinerary map was created")
    return codes[0]


def _endpoint(points: list[dict[str, Any]], code: str, event: str) -> int:
    matches = []
    allowed = {"ADVERTISED_OPEN", "ADVERTISED_PICK_UP" if event == "departure" else "ADVERTISED_SET_DOWN"}
    for i, point in enumerate(points):
        location = point.get("location") or {}
        temporal = point.get("temporalData") or {}
        if code in (location.get("shortCodes") or []) + (location.get("longCodes") or []):
            if temporal.get("scheduledCallType") in allowed and temporal.get(event):
                matches.append(i)
    if len(matches) != 1:
        raise ValueError(f"{code} must identify one advertised {event} call on the selected service")
    return matches[0]


def _scheduled(event: dict[str, Any]) -> datetime:
    value = event.get("scheduleAdvertised") or event.get("scheduleInternal")
    if not value:
        raise ValueError("A journey endpoint has no scheduled time")
    return datetime.fromisoformat(value)


def build_journey_route(tools: Any, engine: Any, legs_json: str) -> dict[str, Any]:
    if len(legs_json) > 8000:
        raise ValueError("Journey input is too large")
    legs = json.loads(legs_json)
    if not isinstance(legs, list) or not 1 <= len(legs) <= 6:
        raise ValueError("Supply 1 to 6 ordered journey legs")
    guidance, required, evidence, warnings = [], [], [], []
    previous_destination = previous_arrival = None
    first_point = last_point = None
    for leg in legs:
        if not isinstance(leg, dict) or set(leg) != {"unique_identity", "origin", "destination"}:
            raise ValueError("Each leg requires only unique_identity, origin and destination")
        if any(not isinstance(v, str) or not v.strip() for v in leg.values()):
            raise ValueError("Journey leg values must be nonempty strings")
        identity = leg["unique_identity"]
        if not re.fullmatch(r"gb-nr:[A-Z][0-9]{5}:\d{4}-\d{2}-\d{2}", identity):
            raise ValueError("Use the exact dated uniqueIdentity returned by RTT")
        origin = tools.resolve_station(leg["origin"])
        destination = tools.resolve_station(leg["destination"])
        if previous_destination and origin["code"] != previous_destination:
            raise ValueError("Journey legs must join at the same station, in travel order")
        service = tools.get_service_schedule(identity)
        metadata = service.get("scheduleMetadata") or {}
        if metadata.get("uniqueIdentity") != identity:
            raise ValueError("RTT returned a different service identity; no itinerary map was created")
        if metadata.get("inPassengerService") is not True:
            raise ValueError("The selected service is not confirmed as a passenger service")
        points = service.get("locations") or []
        start = _endpoint(points, origin["code"], "departure")
        end = _endpoint(points, destination["code"], "arrival")
        if end <= start:
            raise ValueError("The destination must follow the origin on the selected service")
        section = points[start:end + 1]
        departure = section[0]["temporalData"]["departure"]
        arrival = section[-1]["temporalData"]["arrival"]
        if _scheduled(arrival) <= _scheduled(departure):
            raise ValueError("The leg's arrival must be later than its departure")
        if previous_arrival and _scheduled(departure) <= _scheduled(previous_arrival):
            raise ValueError("The scheduled connection is impossible")
        if previous_arrival:
            warnings.append(f"Connection at {origin['name']}: minimum interchange time has not been verified; train times do not prove passenger transfer.")
            before = previous_arrival.get("realtimeActual") or previous_arrival.get("realtimeForecast")
            after = departure.get("realtimeActual") or departure.get("realtimeForecast")
            if before and after and datetime.fromisoformat(after) <= datetime.fromisoformat(before):
                warnings.append(f"The latest running times do not allow the connection at {origin['name']}.")
        leg_calls = []
        for point in section:
            code = _code(point)
            if not guidance or guidance[-1] != code:
                guidance.append(code)
            temporal = point.get("temporalData") or {}
            if str(temporal.get("scheduledCallType", "")).startswith("ADVERTISED_"):
                if not required or required[-1] != code:
                    required.append(code)
                leg_calls.append({"code": code, "name": (point.get("location") or {}).get("description")})
            if any(isinstance(v, dict) and v.get("isCancelled") is True for v in temporal.values()):
                warnings.append(f"{identity}: RTT reports a cancelled movement at {code}; this map does not confirm the journey can be made.")
        evidence.append({"uniqueIdentity": identity, "origin": origin, "destination": destination,
                         "departure": departure, "arrival": arrival, "calls": leg_calls})
        if first_point is None:
            first_point = section[0]
        last_point = section[-1]
        previous_destination, previous_arrival = destination["code"], arrival
    origin_name = evidence[0]["origin"]["name"]
    destination_name = evidence[-1]["destination"]["name"]
    result = engine.route_schedule(
        origin_name + " Rail Station", destination_name + " Rail Station",
        _code(first_point), _code(last_point), required[1:-1], guidance,
    )
    result.update({"origin": origin_name, "destination": destination_name,
                   "routeBasis": "RTT schedule points with inferred topology between points",
                   "legs": evidence, "minimumConnectionTimesVerified": False})
    result["routing_warnings"] = list(dict.fromkeys(warnings + result.get("routing_warnings", [])))
    return result
