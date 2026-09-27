import contextlib
import copy
import io
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from tempfile import TemporaryDirectory
from urllib.request import Request, urlopen
from unittest.mock import patch

from rtt_app.action_api import ActionApplication, make_handler
from rtt_app.journey_route import build_journey_route
from rtt_app.movebook_route import MovebookRouteEngine, MovebookRouteError


def point(code, hour, *, passenger=True):
    event = {"scheduleAdvertised": f"2026-09-19T{hour}:00", "isCancelled": False}
    temporal = {"arrival": dict(event), "departure": dict(event)}
    if passenger:
        temporal["scheduledCallType"] = "ADVERTISED_OPEN"
    else:
        temporal = {"pass": event}
    return {"location": {"shortCodes": [code], "longCodes": [code], "description": code},
            "temporalData": temporal}


class Tools:
    def __init__(self):
        self.services = {
            "gb-nr:A00001:2026-09-19": [point("EDB", "11:24"), point("DIVERT", "12:00", passenger=False),
                                         point("CAR", "12:44"), point("NCL", "14:19")],
            "gb-nr:A00002:2026-09-19": [point("NCL", "14:41"), point("YRK", "15:44"),
                                         point("LDS", "16:10"), point("PLY", "21:47")],
        }

    def resolve_station(self, value):
        return {"name": value, "code": value}

    def get_service_schedule(self, identity):
        return {"scheduleMetadata": {"uniqueIdentity": identity, "inPassengerService": True},
                "locations": self.services[identity]}


class Engine:
    def route_schedule(self, origin, destination, origin_code, destination_code, calls, guidance):
        self.calls, self.guidance = calls, guidance
        return {"mileage": 700, "coordinates": [[55, -3], [50, -4]], "points": [],
                "requested_route_guidance": guidance, "routing_warnings": ["Test topology warning"]}


class JourneyRouteTests(unittest.TestCase):
    def setUp(self):
        self.tools, self.engine = Tools(), Engine()
        self.legs = [{"unique_identity": "gb-nr:A00001:2026-09-19", "origin": "EDB", "destination": "NCL"},
                     {"unique_identity": "gb-nr:A00002:2026-09-19", "origin": "NCL", "destination": "PLY"}]

    def run_route(self):
        return build_journey_route(self.tools, self.engine, json.dumps(self.legs))

    def test_diversion_and_all_calls_are_preserved_across_interchange(self):
        route = self.run_route()
        self.assertEqual(self.engine.guidance, ["EDB", "DIVERT", "CAR", "NCL", "YRK", "LDS", "PLY"])
        self.assertEqual(self.engine.calls, ["CAR", "NCL", "YRK", "LDS"])
        self.assertFalse(route["minimumConnectionTimesVerified"])
        self.assertIn("Test topology warning", route["routing_warnings"])
        self.assertEqual(len(route["legs"]), 2)

    def test_repeated_passing_points_preserve_reversal(self):
        points = self.tools.services[self.legs[0]["unique_identity"]]
        points.insert(3, point("DIVERT", "13:00", passenger=False))
        self.run_route()
        self.assertEqual(self.engine.guidance.count("DIVERT"), 2)

    def test_only_selected_segment_is_mapped(self):
        self.legs = [self.legs[1]]
        self.legs[0]["origin"] = "YRK"
        self.run_route()
        self.assertEqual(self.engine.guidance, ["YRK", "LDS", "PLY"])

    def test_rejects_disconnected_and_reversed_legs(self):
        self.legs[1]["origin"] = "YRK"
        with self.assertRaisesRegex(ValueError, "join"):
            self.run_route()
        self.legs = [self.legs[0]]
        self.legs[0].update(origin="NCL", destination="EDB")
        with self.assertRaisesRegex(ValueError, "follow"):
            self.run_route()

    def test_rejects_impossible_timetable_connection(self):
        self.tools.services[self.legs[1]["unique_identity"]][0] = point("NCL", "14:00")
        with self.assertRaisesRegex(ValueError, "connection is impossible"):
            self.run_route()

    def test_cancellation_and_live_missed_connection_are_warned(self):
        first = self.tools.services[self.legs[0]["unique_identity"]][-1]["temporalData"]["arrival"]
        first["realtimeActual"] = "2026-09-19T15:00:00"
        second = self.tools.services[self.legs[1]["unique_identity"]][0]["temporalData"]["departure"]
        second.update(realtimeActual="2026-09-19T14:42:00", isCancelled=True)
        warnings = " ".join(self.run_route()["routing_warnings"])
        self.assertIn("do not allow", warnings)
        self.assertIn("cancelled", warnings)

    def test_rejects_set_down_only_boarding_and_ambiguous_endpoint(self):
        points = self.tools.services[self.legs[0]["unique_identity"]]
        points[0]["temporalData"]["scheduledCallType"] = "ADVERTISED_SET_DOWN"
        with self.assertRaisesRegex(ValueError, "advertised departure"):
            self.run_route()
        points[0]["temporalData"]["scheduledCallType"] = "ADVERTISED_OPEN"
        points.insert(1, copy.deepcopy(points[0]))
        with self.assertRaisesRegex(ValueError, "one advertised"):
            self.run_route()

    def test_rejects_wrong_identity_and_missing_codes(self):
        original = self.tools.get_service_schedule
        self.tools.get_service_schedule = lambda uid: {**original(uid), "scheduleMetadata": {"uniqueIdentity": "different"}}
        with self.assertRaisesRegex(ValueError, "different service"):
            self.run_route()
        self.tools.get_service_schedule = original
        self.tools.services[self.legs[0]["unique_identity"]][1]["location"] = {}
        with self.assertRaisesRegex(ValueError, "missing or ambiguous"):
            self.run_route()

    def test_rejects_unbounded_and_malformed_input(self):
        for payload in ["{}", "[]", "bad json", json.dumps([self.legs[0]] * 7), json.dumps([{"origin": "EDB"}])]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                build_journey_route(self.tools, self.engine, payload)

    def test_older_engine_cannot_silently_publish_generic_route(self):
        engine = MovebookRouteEngine("unused")
        with patch.object(engine, "_run", return_value={"mileage": 638}):
            with self.assertRaisesRegex(MovebookRouteError, "did not preserve"):
                engine.route_schedule("Edinburgh", "Plymouth", "EDB", "PLY", ["NCL"], ["EDB", "NCL", "PLY"])

    def test_api_links_snapshot_opt_in_and_request_evidence(self):
        with TemporaryDirectory() as folder:
            app = ActionApplication(self.tools, api_key="test", base_url="https://rail.example",
                                    route_engine=self.engine, map_dir=folder)
            args = {"legs": [json.dumps(self.legs)]}
            auth = {"Authorization": "Bearer test"}
            with patch("rtt_app.railway_service.render_snapshot") as renderer:
                response = app.dispatch("GET", "/v1/journey-route", args, auth)
                self.assertEqual(response.status, 200)
                renderer.assert_not_called()
                result = response.body["result"]
                self.assertNotIn("coordinates", result)
                map_id = result["mapUrl"].rsplit("/", 1)[-1]
                map_response = app.dispatch("GET", "/maps/" + map_id, {}, {})
                self.assertIn("55", map_response.body)
                self.assertEqual(next(iter(response.body)), "requestEvidence")
                self.assertNotIn("mapImageUrl", result)
                self.assertIn(result["mapUrl"], result["interactiveMapMarkdown"])
                self.assertEqual(response.body["requestEvidence"]["operation"], "getJourneyRoute")
                args["include_snapshot"] = ["true"]
                second = app.dispatch("GET", "/v1/journey-route", args, auth)
                renderer.assert_called_once()
                result = second.body["result"]
                self.assertEqual(result["mapImageUrl"], result["mapUrl"] + ".png")
                self.assertIn(result["mapUrl"] + ")", result["interactiveMapMarkdown"])
                self.assertNotEqual(response.body["requestEvidence"]["requestId"], second.body["requestEvidence"]["requestId"])

    def test_http_trace_matches_response_without_credentials_or_query(self):
        app = ActionApplication(self.tools, api_key="test-secret", base_url="https://rail.example")
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
        output = io.StringIO()
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        try:
            with contextlib.redirect_stdout(output):
                worker.start()
                request = Request(f"http://127.0.0.1:{server.server_port}/v1/usage?private=do-not-log",
                                  headers={"Authorization": "Bearer test-secret"})
                with urlopen(request) as response:
                    body = json.load(response)
                server.shutdown()
                worker.join()
            event = json.loads(output.getvalue())
            self.assertEqual(event["requestId"], body["requestEvidence"]["requestId"])
            self.assertEqual(event["status"], 200)
            self.assertNotIn("test-secret", output.getvalue())
            self.assertNotIn("do-not-log", output.getvalue())
        finally:
            server.server_close()


if __name__ == "__main__":
    unittest.main()
