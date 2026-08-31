"""Higher-level departure board functionality."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any

from .client import RTTClient, RTTError


@dataclass(frozen=True)
class Departure:
    scheduled: str
    expected: str
    destination: str
    allocation: str
    status: str
    platform: str


@dataclass(frozen=True)
class DepartureBoard:
    station: str
    station_code: str
    departures: list[Departure]


def _normalise(value: str) -> str:
    return " ".join(value.casefold().replace("-", " ").split())


def resolve_station(stops: list[dict[str, Any]], name: str) -> dict[str, Any]:
    """Resolve a station name or code, rejecting genuinely ambiguous matches."""
    query = _normalise(name)
    candidates = [
        stop
        for stop in stops
        if stop.get("namespace", "gb-nr") == "gb-nr" and stop.get("shortCode")
    ]
    exact = [
        stop
        for stop in candidates
        if query
        in {
            _normalise(str(stop.get("description", ""))),
            _normalise(str(stop.get("shortCode", ""))),
        }
    ]
    if exact:
        return exact[0]

    contains = [
        stop
        for stop in candidates
        if query in _normalise(str(stop.get("description", "")))
    ]
    unique_contains = _unique_stations(contains)
    if len(unique_contains) == 1:
        return unique_contains[0]
    if len(unique_contains) > 1:
        suggestions = ", ".join(
            f"{item['description']} ({item['shortCode']})"
            for item in unique_contains[:5]
        )
        raise ValueError(f"Station name is ambiguous. Did you mean: {suggestions}?")

    ranked = sorted(
        candidates,
        key=lambda stop: SequenceMatcher(
            None, query, _normalise(str(stop.get("description", "")))
        ).ratio(),
        reverse=True,
    )
    suggestions = ", ".join(
        f"{item['description']} ({item['shortCode']})"
        for item in _unique_stations(ranked)[:5]
    )
    suffix = f" Did you mean: {suggestions}?" if suggestions else ""
    raise ValueError(f"Station not found: {name}.{suffix}")


def _unique_stations(stops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, Any]] = []
    for stop in stops:
        key = (str(stop.get("description")), str(stop.get("shortCode")))
        if key not in seen:
            seen.add(key)
            result.append(stop)
    return result


def _time_value(temporal: dict[str, Any]) -> dict[str, Any]:
    return temporal.get("departure") or temporal.get("arrival") or temporal.get("pass") or {}


def _clock(value: Any) -> str:
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).strftime("%H:%M")
    except ValueError:
        return str(value)


def _destination(service: dict[str, Any]) -> str:
    names = []
    for pair in service.get("destination") or []:
        location = pair.get("location") or {}
        if location.get("description"):
            names.append(str(location["description"]))
    return " / ".join(names) or "Unknown"


def _is_cancelled(service: dict[str, Any], timing: dict[str, Any]) -> bool:
    temporal = service.get("temporalData") or {}
    if timing.get("isCancelled") or temporal.get("displayAs") in {"CANCELLED", "DIVERTED"}:
        return True
    return any(reason.get("type") == "CANCEL" for reason in service.get("reasons") or [])


def _status(service: dict[str, Any], timing: dict[str, Any]) -> str:
    if _is_cancelled(service, timing):
        return "Cancelled"
    lateness = timing.get("realtimeAdvertisedLateness")
    if lateness is None:
        lateness = timing.get("realtimeInternalLateness")
    if isinstance(lateness, (int, float)):
        if lateness > 0:
            return f"{int(lateness)} min late"
        if lateness < 0:
            return f"{abs(int(lateness))} min early"
        return "On time"
    scheduled = timing.get("scheduleAdvertised") or timing.get("scheduleInternal")
    realtime = (
        timing.get("realtimeActual")
        or timing.get("realtimeForecast")
        or timing.get("realtimeEstimate")
    )
    if scheduled and realtime:
        try:
            scheduled_dt = datetime.fromisoformat(str(scheduled).replace("Z", "+00:00"))
            realtime_dt = datetime.fromisoformat(str(realtime).replace("Z", "+00:00"))
            difference = round((realtime_dt - scheduled_dt).total_seconds() / 60)
            if difference > 0:
                return f"{difference} min late"
            if difference < 0:
                return f"{abs(difference)} min early"
            return "On time"
        except ValueError:
            pass
    if timing.get("realtimeNoReport"):
        return "No report"
    return "On time" if timing.get("realtimeForecast") or timing.get("realtimeActual") else "Scheduled"


def _allocation(service_data: dict[str, Any], location_metadata: dict[str, Any]) -> str:
    allocations = service_data.get("allocationData") or []
    wanted_index = location_metadata.get("allocationIndex")
    allocation = next(
        (item for item in allocations if item.get("allocationIndex") == wanted_index),
        allocations[0] if allocations else None,
    )
    if not allocation:
        branding = location_metadata.get("stockBranding")
        vehicles = location_metadata.get("numberOfVehicles")
        if branding and vehicles:
            return f"{branding} ({vehicles} coaches)"
        if branding:
            return str(branding)
        if vehicles:
            return f"{vehicles} coaches; type not available"
        return "Not available"

    identities = [
        str(item["identity"])
        for item in allocation.get("allocationItems") or []
        if item.get("identity") and not item.get("identitySuppressed")
    ]
    leading_class = allocation.get("leadingClass")
    vehicles = allocation.get("passengerVehicles")
    if identities:
        value = " + ".join(identities)
    elif leading_class:
        value = f"Class {leading_class}"
    else:
        value = "Type not available"
    if vehicles:
        value += f" ({vehicles} coaches)"
    return value


def next_departures(
    client: RTTClient, station_name: str, *, limit: int = 5, minutes: int = 180
) -> DepartureBoard:
    """Resolve a station and return the next passenger departures."""
    stops_data = client.stops().data or {}
    station = resolve_station(stops_data.get("stops") or [], station_name)
    lineup_response = client.location(
        str(station["shortCode"]), time_window=minutes, detailed=True
    )
    services = (lineup_response.data or {}).get("services") or []
    departures: list[Departure] = []
    for service in services:
        schedule = service.get("scheduleMetadata") or {}
        if schedule.get("inPassengerService") is False:
            continue
        temporal = service.get("temporalData") or {}
        timing = temporal.get("departure")
        if not timing:
            continue
        location_metadata = service.get("locationMetadata") or {}
        allocation = "Not available"
        unique_id = schedule.get("uniqueIdentity")
        if unique_id:
            # The gb-nr service endpoint expects the identity without its namespace.
            nr_unique_id = str(unique_id).removeprefix("gb-nr:")
            try:
                detail = client.service(unique_identity=nr_unique_id, detailed=True).data or {}
                allocation = _allocation(detail.get("service") or {}, location_metadata)
            except RTTError as exc:
                if exc.status not in {401, 403, 404}:
                    raise
        scheduled_value = timing.get("scheduleAdvertised") or timing.get("scheduleInternal")
        expected_value = (
            timing.get("realtimeActual")
            or timing.get("realtimeForecast")
            or timing.get("realtimeEstimate")
            or scheduled_value
        )
        platform = location_metadata.get("platform") or {}
        departures.append(
            Departure(
                scheduled=_clock(scheduled_value),
                expected="Cancelled" if _is_cancelled(service, timing) else _clock(expected_value),
                destination=_destination(service),
                allocation=allocation,
                status=_status(service, timing),
                platform=str(platform.get("actual") or platform.get("forecast") or platform.get("planned") or "—"),
            )
        )
        if len(departures) >= limit:
            break
    return DepartureBoard(
        station=str(station.get("description") or station_name),
        station_code=str(station["shortCode"]),
        departures=departures,
    )


def format_board(board: DepartureBoard) -> str:
    """Render a compact terminal-friendly departure board."""
    headers = ("Time", "Expected", "Destination", "Platform", "Allocation", "Status")
    rows = [
        (d.scheduled, d.expected, d.destination, d.platform, d.allocation, d.status)
        for d in board.departures
    ]
    if not rows:
        return f"{board.station} ({board.station_code})\nNo departures found."
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    ]
    render = lambda row: "  ".join(value.ljust(widths[index]) for index, value in enumerate(row)).rstrip()
    return "\n".join(
        [
            f"Next departures from {board.station} ({board.station_code})",
            render(headers),
            render(tuple("-" * width for width in widths)),
            *(render(row) for row in rows),
        ]
    )
