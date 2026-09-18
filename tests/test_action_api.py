import unittest
from tempfile import TemporaryDirectory

from rtt_app.action_api import ActionApplication, build_openapi_schema


class FakeTools:
    def next_departures(self, station, count, minutes):
        return {"station": station, "count": count, "minutes": minutes}

    def search_station_services(self, **kwargs):
        return kwargs

    def get_service_details(self, unique_identity):
        return {"uniqueIdentity": unique_identity}

    def get_api_info(self):
        return {"version": "test"}

    def resolve_station(self, value):
        codes = {"Bristol Temple Meads": "BRI", "London Paddington": "PAD"}
        return {"name": value, "code": codes.get(value, value)}


class FakeRouteEngine:
    def route(self, origin, destination, via_tiplocs):
        return {
            "origin": origin,
            "destination": destination,
            "origin_tiploc": "BRSTLTM",
            "destination_tiploc": "PADTON",
            "via_tiplocs": via_tiplocs,
            "mileage": 118.5,
            "coordinates": [[51.45, -2.58], [51.52, -0.18]],
            "points": [
                {"role": "origin", "label": origin, "coordinate": [51.45, -2.58]},
                {"role": "destination", "label": destination, "coordinate": [51.52, -0.18]},
            ],
            "candidates": [{"tiploc": "RDNGSTN", "label": "Reading"}],
        }


class ActionApiTests(unittest.TestCase):
    def setUp(self):
        self.app = ActionApplication(
            FakeTools(), api_key="secret-test-key", base_url="https://rail.example.com/"
        )
        self.auth = {"Authorization": "Bearer secret-test-key"}

    def test_schema_has_unique_operation_ids_and_https_server(self):
        schema = build_openapi_schema("https://rail.example.com/")
        self.assertEqual(schema["servers"][0]["url"], "https://rail.example.com")
        ids = [operation["get"]["operationId"] for operation in schema["paths"].values()]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("getServiceDetails", ids)
        self.assertIn("getApiUsage", ids)
        self.assertIn("suggestRailRoute", ids)
        usage_schema = schema["paths"]["/v1/usage"]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        self.assertEqual(
            usage_schema["properties"]["result"]["properties"]["totalRequests"]["type"],
            "integer",
        )

    def test_schema_meets_reported_gpt_action_import_constraints(self):
        schema = build_openapi_schema("https://rail.example.com")
        self.assertIsInstance(schema["components"]["schemas"], dict)
        self.assertEqual(schema["components"]["securitySchemes"], {
            "bearerAuth": {"type": "http", "scheme": "bearer"},
        })
        self.assertEqual(schema["security"], [{"bearerAuth": []}])
        for path, methods in schema["paths"].items():
            for method, operation in methods.items():
                with self.subTest(path=path, method=method):
                    self.assertLessEqual(len(operation.get("description", "")), 300)

    def test_health_and_schema_do_not_require_authentication(self):
        self.assertEqual(self.app.dispatch("GET", "/health", {}, {}).status, 200)
        self.assertEqual(self.app.dispatch("GET", "/openapi.json", {}, {}).status, 200)

    def test_action_endpoints_require_authentication(self):
        response = self.app.dispatch("GET", "/v1/info", {}, {})
        self.assertEqual(response.status, 401)

    def test_departures_dispatches_validated_arguments(self):
        response = self.app.dispatch(
            "GET",
            "/v1/departures",
            {"station": ["BRI"], "count": ["5"], "minutes": ["120"]},
            self.auth,
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body["result"]["station"], "BRI")
        self.assertEqual(response.body["result"]["minutes"], 120)

    def test_rejects_invalid_count(self):
        response = self.app.dispatch(
            "GET", "/v1/departures", {"station": ["BRI"], "count": ["99"]}, self.auth
        )
        self.assertEqual(response.status, 400)

    def test_time_to_omits_minutes(self):
        response = self.app.dispatch(
            "GET",
            "/v1/services",
            {"station": ["PAD"], "time_to": ["2026-08-29T20:00:00+01:00"]},
            self.auth,
        )
        self.assertEqual(response.status, 200)
        self.assertIsNone(response.body["result"]["minutes"])

    def test_usage_reports_aggregate_counts(self):
        self.app.record_request("/v1/departures", 200)
        self.app.record_request("/v1/services", 401)
        self.app.record_request("/health", 200)

        response = self.app.dispatch("GET", "/v1/usage", {}, self.auth)

        self.assertEqual(response.status, 200)
        usage = response.body["result"]
        self.assertEqual(usage["totalRequests"], 2)
        self.assertEqual(usage["requestsByEndpoint"]["/v1/departures"], 1)
        self.assertEqual(usage["responsesByStatus"], {"200": 1, "401": 1})

    def test_route_returns_movebook_result_and_public_map(self):
        with TemporaryDirectory() as map_dir:
            app = ActionApplication(
                FakeTools(),
                api_key="secret-test-key",
                base_url="https://rail.example.com",
                route_engine=FakeRouteEngine(),
                map_dir=map_dir,
            )
            response = app.dispatch(
                "GET",
                "/v1/route",
                {
                    "origin": ["Bristol Temple Meads"],
                    "destination": ["London Paddington"],
                    "via": ["RDNGSTN"],
                },
                self.auth,
            )
            self.assertEqual(response.status, 200)
            self.assertEqual(response.body["result"]["via_tiplocs"], ["RDNGSTN"])
            self.assertEqual(response.body["result"]["originCode"], "BRI")
            map_url = response.body["result"]["mapUrl"]
            self.assertTrue(map_url.startswith("https://rail.example.com/maps/"))
            map_response = app.dispatch("GET", "/maps/" + map_url.rsplit("/", 1)[-1], {}, {})
            self.assertEqual(map_response.status, 200)
            self.assertIn("Bristol Temple Meads", map_response.body)


if __name__ == "__main__":
    unittest.main()
