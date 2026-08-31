import unittest

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


if __name__ == "__main__":
    unittest.main()
