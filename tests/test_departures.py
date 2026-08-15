import unittest

from rtt_app.client import RTTResponse
from rtt_app.departures import format_board, next_departures, resolve_station


def response(data):
    return RTTResponse(data=data, status=200, rate_limits={})


class FakeClient:
    def stops(self):
        return response(
            {"stops": [{"namespace": "gb-nr", "description": "Clapham Junction", "shortCode": "CLJ"}]}
        )

    def location(self, code, **kwargs):
        return response(
            {
                "services": [
                    {
                        "scheduleMetadata": {"uniqueIdentity": "gb-nr:L01525:2026-08-13", "inPassengerService": True},
                        "temporalData": {"departure": {"scheduleAdvertised": "2026-08-13T14:00:00+01:00", "realtimeForecast": "2026-08-13T14:07:00+01:00", "realtimeAdvertisedLateness": 7}},
                        "locationMetadata": {"allocationIndex": 0, "platform": {"planned": "5"}},
                        "destination": [{"location": {"description": "London Waterloo"}}],
                    },
                    {
                        "scheduleMetadata": {"uniqueIdentity": "gb-nr:C00001:2026-08-13", "inPassengerService": True},
                        "temporalData": {"departure": {"scheduleAdvertised": "2026-08-13T14:05:00+01:00", "isCancelled": True}},
                        "locationMetadata": {},
                        "destination": [{"location": {"description": "Woking"}}],
                        "reasons": [{"type": "CANCEL", "shortText": "Train fault"}],
                    },
                ]
            }
        )

    def service(self, **kwargs):
        if kwargs["unique_identity"].startswith("C00001"):
            return response({"service": {}})
        return response(
            {"service": {"allocationData": [{"allocationIndex": 0, "leadingClass": "444", "passengerVehicles": 10, "allocationItems": [{"stockType": "UNIT", "identity": "444045"}]}]}}
        )


class DepartureTests(unittest.TestCase):
    def test_resolves_names_case_insensitively(self):
        stop = resolve_station(
            [{"namespace": "gb-nr", "description": "London Waterloo", "shortCode": "WAT"}],
            "london waterloo",
        )
        self.assertEqual(stop["shortCode"], "WAT")

    def test_builds_enriched_departure_board(self):
        board = next_departures(FakeClient(), "Clapham Junction")
        self.assertEqual(len(board.departures), 2)
        self.assertEqual(board.departures[0].expected, "14:07")
        self.assertEqual(board.departures[0].allocation, "444045 (10 coaches)")
        self.assertEqual(board.departures[0].status, "7 min late")
        self.assertEqual(board.departures[1].status, "Cancelled")
        self.assertIn("London Waterloo", format_board(board))


if __name__ == "__main__":
    unittest.main()
