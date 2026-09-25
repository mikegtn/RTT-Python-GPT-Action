import asyncio
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import unittest

from rtt_app.mcp_tools import RailWorkflows, passenger_leg, timestamp, tool_catalog


def service(identity="opaque-rtt-identity", origin="ABD", destination="PLY", departure="08:00", arrival="18:00"):
    return {"scheduleMetadata": {"uniqueIdentity": identity, "inPassengerService": True},
            "calls": [
                {"location": {"description": origin, "shortCodes": [origin]},
                 "temporalData": {"scheduledCallType": "ADVERTISED_PICK_UP", "departure": {
                     "scheduleAdvertised": f"2026-09-19T{departure}:00+01:00"}}},
                {"location": {"description": destination, "shortCodes": [destination]},
                 "temporalData": {"scheduledCallType": "ADVERTISED_SET_DOWN", "arrival": {
                     "scheduleAdvertised": f"2026-09-19T{arrival}:00+01:00"}}}]}


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    def test_rtt_local_times_and_clock_changes(self):
        self.assertEqual(timestamp("2026-09-19T05:57:00"), timestamp("2026-09-19T05:57:00+01:00"))
        with self.assertRaises(ValueError):
            timestamp("2026-10-25T01:30:00")
        with self.assertRaises(ValueError):
            timestamp("2026-03-29T01:30:00")
        with self.assertRaises(ValueError):
            timestamp("2026-09-19T05:57:00", require_offset=True)

    def test_cancelled_and_restricted_trains_are_excluded(self):
        for mutation in [
            lambda s: s["calls"][0]["temporalData"]["departure"].update(isCancelled=True),
            lambda s: s["calls"][1]["temporalData"].update(realtimeCallType="CANCELLED_CALL"),
            lambda s: s["calls"][0]["temporalData"].update(scheduledCallType="ADVERTISED_SET_DOWN"),
            lambda s: s["scheduleMetadata"].update(inPassengerService=False),
            lambda s: s["calls"][0]["temporalData"]["departure"].pop("scheduleAdvertised"),
        ]:
            data = service()
            mutation(data)
            self.assertIsNone(passenger_leg(data, "opaque-rtt-identity", "ABD", "PLY"))

    async def test_proxy_preserves_evidence_identity_and_native_legs(self):
        expected = {"ok": True, "requestEvidence": {"requestId": "exact-id"},
                    "result": {"uniqueIdentity": "opaque-rtt-identity"}}
        async def backend(path, params):
            self.assertEqual(path, "/v1/journey-route")
            self.assertEqual(json.loads(params["legs"])[0]["unique_identity"], "opaque-rtt-identity")
            return expected
        result = await RailWorkflows(backend).call("getJourneyRoute", {"legs": [
            {"unique_identity": "opaque-rtt-identity", "origin": "ABD", "destination": "PLY"}]})
        self.assertIs(result, expected)

    async def test_location_uses_actual_never_forecast(self):
        data = service()
        data["calls"][0]["temporalData"]["departure"]["realtimeActual"] = "2020-09-19T08:01:00+01:00"
        data["calls"][1]["temporalData"]["arrival"]["realtimeForecast"] = "2020-09-19T18:02:00+01:00"
        async def backend(path, params):
            return {"ok": True, "result": data, "requestEvidence": {"requestId": "source"}}
        result = await RailWorkflows(backend).call("getTrainLocation", {"unique_identity": "opaque-rtt-identity"})
        self.assertEqual(result["result"]["lastReport"]["location"]["shortCodes"], ["ABD"])
        self.assertEqual(result["sourceRequestEvidence"], [{"requestId": "source"}])
        self.assertGreater(result["result"]["reportAgeSeconds"], 0)
        data["calls"][0]["temporalData"]["isInterpolated"] = True
        result = await RailWorkflows(backend).call("getTrainLocation", {"unique_identity": "opaque-rtt-identity"})
        self.assertIsNone(result["result"]["lastReport"])

    async def test_identity_mismatch_fails_closed(self):
        async def backend(path, params):
            return {"ok": True, "result": service("different")}
        result = await RailWorkflows(backend).call("getRouteDetails", {"unique_identity": "expected"})
        self.assertFalse(result["ok"])

    async def test_journeys_check_connections_and_preserve_sources(self):
        data = {"first": service("first", "ABD", "EDB", "08:00", "10:00"),
                "too-early": service("too-early", "EDB", "PLY", "10:05", "18:00"),
                "valid": service("valid", "EDB", "PLY", "10:20", "18:15")}
        async def backend(path, params):
            if path == "/v1/service":
                result = data[params["unique_identity"]]
            else:
                ids = [] if params["filter_to"] == "PLY" and params["station"] == "ABD" else (
                    ["first"] if params["station"] == "ABD" else ["too-early", "valid"])
                result = {"services": [{"scheduleMetadata": {"uniqueIdentity": i}} for i in ids]}
            return {"ok": True, "result": result, "requestEvidence": {"requestId": str(len(params))}}
        result = await RailWorkflows(backend).call("findJourneys", {
            "origin": "ABD", "destination": "PLY", "time_from": "2026-09-19T07:00:00+01:00",
            "interchanges": ["EDB"]})
        self.assertTrue(result["ok"])
        itineraries = result["result"]["itineraries"]
        self.assertEqual(len(itineraries), 1)
        self.assertEqual([l["uniqueIdentity"] for l in itineraries[0]["legs"]], ["first", "valid"])
        self.assertEqual(itineraries[0]["connectionMinutes"], 20)
        self.assertFalse(result["result"]["minimumConnectionTimesVerified"])
        self.assertEqual(len(result["sourceRequestEvidence"]), 6)


@unittest.skipUnless(importlib.util.find_spec("mcp"), "Install the mcp extra for transport tests")
class TransportTests(unittest.TestCase):
    def setUp(self):
        from starlette.testclient import TestClient
        from rtt_app.mcp_server import create_server, create_http_app
        self.calls = []
        async def backend(path, params):
            self.calls.append((path, params))
            return {"ok": True, "result": service(params.get("unique_identity", "opaque")),
                    "requestEvidence": {"requestId": "unchanged", "operation": "getServiceDetails"}}
        self.client = TestClient(create_http_app(create_server(backend)))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.headers = {"Accept": "application/json, text/event-stream"}

    def rpc(self, method, params=None):
        return self.client.post("/mcp", headers=self.headers,
                                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})

    def test_public_endpoint_does_not_require_credentials(self):
        response = self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                          "clientInfo": {"name": "test", "version": "1"}})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.calls, [])

    def test_initialize_list_call_and_invalid_input(self):
        response = self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                          "clientInfo": {"name": "test", "version": "1"}})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["result"]["serverInfo"]["name"], "realtime-trains")
        catalog = self.rpc("tools/list").json()["result"]["tools"]
        self.assertEqual(len(catalog), 12)
        by_name = {t["name"]: t for t in catalog}
        self.assertFalse(by_name["getRailMapSnapshot"]["annotations"]["readOnlyHint"])
        for tool in catalog:
            self.assertEqual(tool["securitySchemes"], [{"type": "noauth"}])
            self.assertEqual(tool["_meta"]["securitySchemes"], [{"type": "noauth"}])
            self.assertIn("outputSchema", tool)
            self.assertNotEqual(tool["outputSchema"], {
                "type": "object", "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"], "additionalProperties": True})
        called = self.rpc("tools/call", {"name": "getServiceDetails", "arguments": {"unique_identity": "opaque"}}).json()["result"]
        self.assertEqual(called["structuredContent"]["scheduleMetadata"]["uniqueIdentity"], "opaque")
        self.assertNotIn("requestEvidence", called["structuredContent"])
        self.assertNotIn("sourceRequestEvidence", called["structuredContent"])
        invalid = self.rpc("tools/call", {"name": "getServiceDetails", "arguments": {"unique_identity": 123}}).json()["result"]
        self.assertTrue(invalid["isError"])
        self.assertEqual(len(self.calls), 1)

    def test_untrusted_origin_is_rejected(self):
        self.headers["Origin"] = "https://attacker.example"
        self.assertEqual(self.rpc("tools/list").status_code, 403)

    def test_backend_requires_secure_destination(self):
        from rtt_app.mcp_server import ActionBackend
        with self.assertRaises(ValueError):
            ActionBackend("secret", "http://example.com")
        with self.assertRaises(ValueError):
            ActionBackend("secret", "https://user:password@example.com")


if __name__ == "__main__":
    unittest.main()
