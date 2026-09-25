from copy import deepcopy
import unittest

from rtt_app.mcp_tools import RailWorkflows, tool_catalog
from rtt_app.service_progress import service_progress


def fixture():
    def call(name, arrival=None, departure=None):
        temporal = {"scheduledCallType": "ADVERTISED_OPEN", "isInterpolated": False}
        for event, actual in (("arrival", arrival), ("departure", departure)):
            if actual:
                temporal[event] = {"realtimeActual": "2026-09-19T" + actual,
                    "scheduleAdvertised": "2026-09-19T20:00:00", "realtimeAdvertisedLateness": 13}
        return {"location": {"description": name}, "temporalData": temporal}
    return {"scheduleMetadata": {"uniqueIdentity": "opaque"},
            "destination": [{"description": "Plymouth"}],
            "calls": [call("Taunton", "20:29:15", "20:30:30"),
                      call("Tiverton Parkway", "20:41:45", "20:43:30"),
                      call("Plymouth", "21:55:15")]}


class ProgressTests(unittest.TestCase):
    def progress(self, data=None, at="20:35:00"):
        return service_progress(data or fixture(), "opaque", "2026-09-19T" + at + "+01:00")

    def test_states_and_inclusive_boundaries(self):
        for at, state in [("20:29:14", "not_started"), ("20:29:15", "at_station"),
                          ("20:30:29", "at_station"), ("20:30:30", "between_calls"),
                          ("20:41:44", "between_calls"), ("20:41:45", "at_station"),
                          ("21:55:14", "between_calls"), ("21:55:15", "completed")]:
            with self.subTest(at=at):
                self.assertEqual(self.progress(at=at)["state"], state)
        result = self.progress()
        self.assertEqual(result["from"]["name"], "Taunton")
        self.assertEqual(result["to"]["name"], "Tiverton Parkway")
        self.assertIsNone(result["to"]["actualArrival"])
        self.assertIsNone(result["to"]["actualDeparture"])
        self.assertEqual(result["latenessMinutes"], 13)
        self.assertEqual(result["positionBasis"], "RTT actual movement reports; not GPS")

    def test_forecasts_and_interpolation_are_not_observations(self):
        data = fixture()
        for call in data["calls"]:
            for value in call["temporalData"].values():
                if isinstance(value, dict):
                    value["realtimeForecast"] = value.pop("realtimeActual")
        self.assertEqual(self.progress(data)["state"], "not_started")
        data = fixture()
        data["calls"][0]["temporalData"]["isInterpolated"] = True
        self.assertEqual(self.progress(data)["state"], "not_started")

    def test_cancelled_and_technical_next_calls_are_skipped(self):
        for mutation in [{"displayAs": "CANCELLED"}, {"realtimeCallType": "CANCELLED_CALL"},
                         {"realtimeCallType": "OPERATIONAL"}, {"displayAs": "DIVERTED"},
                         {"arrival": {"isCancelled": True}}]:
            data = fixture()
            data["calls"][1]["temporalData"].update(mutation)
            self.assertEqual(self.progress(data)["to"]["name"], "Plymouth")

    def test_pass_does_not_place_train_at_station(self):
        data = fixture()
        passing = {"location": {"description": "Timing point"}, "temporalData": {
            "scheduledCallType": "PASS", "pass": {"realtimeActual": "2026-09-19T20:33:00"}}}
        data["calls"].insert(1, passing)
        result = self.progress(data)
        self.assertEqual(result["state"], "between_calls")
        self.assertEqual(result["from"]["name"], "Taunton")
        self.assertEqual(result["lastReport"]["event"], "pass")
        # A later pass contradicts continued at_station even with a missing departure.
        del data["calls"][0]["temporalData"]["departure"]["realtimeActual"]
        with self.assertRaises(ValueError):
            self.progress(data)

    def test_missing_destination_arrival_never_completes(self):
        data = fixture()
        data["calls"][-1]["temporalData"]["arrival"].pop("realtimeActual")
        self.assertEqual(self.progress(data, "22:00:00")["state"], "between_calls")
        data["calls"][-1]["temporalData"]["arrival"]["isCancelled"] = True
        with self.assertRaises(ValueError):
            self.progress(data, "22:00:00")

    def test_ambiguous_invalid_and_conflicting_reports_fail_closed(self):
        for value in ["2026-09-19T20:35:00", "invalid", "2099-01-01T00:00:00Z"]:
            with self.assertRaises(ValueError):
                service_progress(fixture(), "opaque", value)
        data = fixture()
        data["calls"][0]["temporalData"]["departure"]["realtimeActual"] = "2026-10-25T01:30:00"
        with self.assertRaises(ValueError):
            self.progress(data)
        data = fixture()
        data["calls"][1]["temporalData"]["arrival"]["realtimeActual"] = "2026-09-19T20:20:00"
        with self.assertRaises(ValueError):
            self.progress(data)

    def test_offsets_and_overnight_dates(self):
        self.assertEqual(service_progress(fixture(), "opaque", "2026-09-19T19:35:00Z")["state"], "between_calls")
        data = fixture()
        data["calls"][-1]["temporalData"]["arrival"]["realtimeActual"] = "2026-09-20T00:10:00"
        self.assertEqual(service_progress(data, "opaque", "2026-09-20T00:11:00+01:00")["state"], "completed")


class ProgressWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_contract_and_evidence(self):
        source = {"requestId": "exact-source", "operation": "getServiceDetails", "completedAt": "exact-time"}
        data = fixture()
        before = deepcopy(data)
        async def backend(path, params):
            self.assertEqual((path, params), ("/v1/service", {"unique_identity": "opaque"}))
            return {"ok": True, "result": data, "requestEvidence": source}
        result = await RailWorkflows(backend).call("getServiceProgress", {
            "unique_identity": "opaque", "as_of": "2026-09-19T20:35:00+01:00"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["uniqueIdentity"], "opaque")
        self.assertEqual(result["sourceRequestEvidence"], [source])
        self.assertEqual(result["requestEvidence"]["operation"], "getServiceProgress")
        self.assertEqual(data, before)
        self.assertTrue(tool_catalog()["getServiceProgress"]["annotations"]["readOnlyHint"])
        data["scheduleMetadata"]["uniqueIdentity"] = "wrong"
        self.assertFalse((await RailWorkflows(backend).call("getServiceProgress", {"unique_identity": "opaque"}))["ok"])
