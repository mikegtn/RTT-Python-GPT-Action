"""Conservative passenger progress from actual RTT events, with an inclusive cutoff."""
from datetime import datetime, timezone

from .mcp_tools import matches, timestamp


PASSENGER_TYPES = {"ADVERTISED_OPEN", "ADVERTISED_PICK_UP", "ADVERTISED_SET_DOWN"}


def passenger_call(call):
    temporal = call.get("temporalData") or {}
    kind = temporal.get("realtimeCallType") or temporal.get("scheduledCallType")
    return (kind in PASSENGER_TYPES
            and temporal.get("displayAs") not in {"CANCELLED", "DIVERTED", "PASS"}
            and not any(isinstance(v, dict) and v.get("isCancelled") is True
                        for v in temporal.values()))


def service_progress(service, identity, as_of=None):
    if (service.get("scheduleMetadata") or {}).get("uniqueIdentity") != identity:
        raise ValueError("RTT returned a different service identity")
    now = datetime.now(timezone.utc)
    cutoff = timestamp(as_of, require_offset=True) if as_of is not None else now
    if cutoff > now:
        raise ValueError("as_of must not be in the future")
    calls = service.get("calls") or []
    if not calls:
        raise ValueError("No RTT calls available to establish service progress")
    events = []
    for index, call in enumerate(calls):
        temporal = call.get("temporalData") or {}
        if temporal.get("isInterpolated") is True:
            continue
        for order, event in enumerate(("arrival", "pass", "departure")):
            timing = temporal.get(event) or {}
            actual = timing.get("realtimeActual")
            if actual and timing.get("isCancelled") is not True:
                instant = timestamp(actual)
                if instant <= cutoff:
                    events.append((instant, index, order, event, timing))
    events.sort(key=lambda e: e[:3])
    if any(a[1] > b[1] for a, b in zip(events, events[1:])):
        raise ValueError("Actual movement reports conflict with RTT call order")

    def point(index):
        call = calls[index]
        result = {"callIndex": index, "name": (call.get("location") or {}).get("description"),
                  "location": call.get("location")}
        for event in ("arrival", "departure"):
            timing = (call.get("temporalData") or {}).get(event) or {}
            label = event.title()
            result["scheduled" + label] = timing.get("scheduleAdvertised")
            result["scheduledInternal" + label] = timing.get("scheduleInternal")
            observed = next((e for e in reversed(events) if e[1] == index and e[3] == event), None)
            result["actual" + label] = observed[4]["realtimeActual"] if observed else None
        return result

    result = {"uniqueIdentity": identity, "state": "not_started", "evaluatedAt": cutoff.isoformat(),
              "retrievedAt": now.isoformat(), "lastReport": None, "latenessMinutes": None,
              "positionBasis": "RTT actual movement reports; not GPS",
              "timeZone": "Europe/London for RTT timestamps without an explicit offset",
              "warnings": ["States describe available movement reports; missing reports do not prove current position."]}
    if as_of is not None:
        result["warnings"].append("Historical replay filters actual event times in the currently retrieved record; historical receipt times and timetable/cancellation revisions are unavailable.")
    if not events:
        result["warnings"].append("No actual movement report at or before evaluation time; not_started does not prove the train has not moved.")
        return result
    latest = events[-1]
    instant, index, _, event, timing = latest
    result["lastReport"] = {"callIndex": index, "location": calls[index].get("location"), "event": event,
                            "reportedAt": timing["realtimeActual"]}
    result["reportAgeSeconds"] = max(0, int((cutoff - instant).total_seconds()))
    result["latenessMinutes"] = timing.get("realtimeAdvertisedLateness")
    result["latenessBasis"] = "RTT realtimeAdvertisedLateness for lastReport"
    # Require an arrival at the declared final destination, not merely the last
    # surviving non-cancelled call (which might precede a cancelled terminus).
    destinations = service.get("destination") or []
    final_matches = [i for i, c in enumerate(calls) if any(
        matches(c, code) for d in destinations
        for code in [*(d.get("longCodes") or []), *(d.get("shortCodes") or []), d.get("description", "")]
        if code)]
    if event == "arrival" and final_matches and index == final_matches[-1]:
        result.update(state="completed", at=point(index))
        return result
    if event == "arrival" and passenger_call(calls[index]):
        # A later departure/pass anywhere would be latest instead of this arrival.
        if any(e[1] == index and e[3] in {"departure", "pass"} for e in events):
            raise ValueError("Conflicting arrival and departure reports at the same call")
        result.update(state="at_station", at=point(index))
        return result
    departures = [e for e in events if e[3] == "departure" and passenger_call(calls[e[1]])]
    if not departures:
        raise ValueError("Actual reports exist but no passenger-station departure supports between_calls")
    departed = departures[-1]
    next_index = next((i for i in range(departed[1] + 1, len(calls)) if passenger_call(calls[i])), None)
    if next_index is None or index >= next_index:
        raise ValueError("Actual reports do not establish an unvisited next passenger call")
    if event == "arrival":
        raise ValueError("Latest actual arrival is not an advertised passenger station")
    result.update(state="between_calls", **{"from": point(departed[1]), "to": point(next_index)})
    result["departureLatenessMinutes"] = departed[4].get("realtimeAdvertisedLateness")
    return result
