import json
import subprocess
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from tempfile import TemporaryDirectory
from unittest.mock import patch

from rtt_app.movebook_route import MovebookRouteEngine, MovebookRouteError


def completed(payload, returncode=0):
    stdout = payload if isinstance(payload, str) else json.dumps(payload)
    return CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


class MovebookRouteInputTests(unittest.TestCase):
    def setUp(self):
        self.engine = MovebookRouteEngine("unused")

    def test_route_trims_input_and_normalizes_via_codes(self):
        with patch.object(self.engine, "_run", return_value={}) as run:
            self.engine.route("  Edinburgh ", " Plymouth  ", [" ncl ", "", "yrk"])
        run.assert_called_once_with(
            {"origin": "Edinburgh", "destination": "Plymouth", "via_tiplocs": ["NCL", "YRK"]})

    def test_route_requires_origin_and_destination(self):
        for origin, destination in [("", "PLY"), ("EDB", "  "), ("  ", "")]:
            with self.subTest(origin=origin, destination=destination):
                with self.assertRaisesRegex(ValueError, "origin and destination are required"):
                    self.engine.route(origin, destination)

    def test_route_rejects_more_than_twelve_vias(self):
        vias = [f"VIA{index:02d}" for index in range(13)]
        with self.assertRaisesRegex(ValueError, "up to 12"):
            self.engine.route("EDB", "PLY", vias)

    def test_route_rejects_duplicate_vias_after_normalization(self):
        with self.assertRaisesRegex(ValueError, "only be selected once"):
            self.engine.route("EDB", "PLY", ["ncl", "NCL"])

    def test_route_schedule_bounds_guidance_length(self):
        for guidance in (["EDB"], ["EDB"] * 2001):
            with self.subTest(points=len(guidance)):
                with self.assertRaisesRegex(ValueError, "2 to 2000"):
                    self.engine.route_schedule("Edinburgh", "Plymouth", "EDB", "PLY", [], guidance)


class MovebookRouteRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        script = Path(self.tmp.name) / "route.py"
        script.write_text("# placeholder; subprocess.run is mocked in these tests\n")
        self.engine = MovebookRouteEngine(script, python="python-under-test", timeout=7)

    def run_with(self, result=None, **kwargs):
        with patch("rtt_app.movebook_route.subprocess.run", return_value=result, **kwargs) as run:
            return run, self.engine.route("EDB", "PLY")

    def test_missing_script_is_reported_as_unavailable(self):
        engine = MovebookRouteEngine(Path(self.tmp.name) / "missing.py")
        with patch("rtt_app.movebook_route.subprocess.run") as run:
            with self.assertRaisesRegex(MovebookRouteError, "unavailable"):
                engine.route("EDB", "PLY")
        run.assert_not_called()

    def test_request_is_sent_as_json_on_stdin_with_timeout(self):
        run, _ = self.run_with(completed({"mileage": 638}))
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["python-under-test", str(self.engine.script)])
        self.assertEqual(json.loads(kwargs["input"]),
                         {"origin": "EDB", "destination": "PLY", "via_tiplocs": []})
        self.assertEqual(kwargs["timeout"], 7)
        self.assertFalse(kwargs["check"])

    def test_timeout_and_os_errors_become_engine_errors(self):
        for error in (subprocess.TimeoutExpired("route.py", 7), OSError("boom")):
            with self.subTest(error=type(error).__name__):
                with patch("rtt_app.movebook_route.subprocess.run", side_effect=error):
                    with self.assertRaisesRegex(MovebookRouteError, "did not respond"):
                        self.engine.route("EDB", "PLY")

    def test_invalid_json_is_rejected(self):
        with self.assertRaisesRegex(MovebookRouteError, "invalid response"):
            self.run_with(completed("not json"))

    def test_non_zero_exit_reports_engine_message(self):
        with self.assertRaisesRegex(MovebookRouteError, "No path between stations"):
            self.run_with(completed({"error": "No path between stations"}, returncode=1))

    def test_error_field_fails_even_with_zero_exit(self):
        with self.assertRaisesRegex(MovebookRouteError, "Unknown TIPLOC"):
            self.run_with(completed({"error": "Unknown TIPLOC"}))

    def test_non_object_or_errorless_failure_uses_generic_message(self):
        for result in (completed([1, 2, 3]), completed({}, returncode=2)):
            with self.subTest(stdout=result.stdout):
                with self.assertRaisesRegex(MovebookRouteError, "No connected railway route"):
                    self.run_with(result)

    def test_candidates_are_reduced_to_labelled_tiplocs(self):
        payload = {"mileage": 638, "candidates": [
            {"tiploc": "NEWCSTL", "name": "Newcastle", "coordinate": [54.97, -1.62], "extra": "dropped"},
            {"tiploc": "YORK", "label": "York", "name": "ignored"},
            {"tiploc": "DRHM"},
            {"name": "no tiploc"},
            "not a mapping",
        ]}
        _, result = self.run_with(completed(payload))
        self.assertEqual(result["mileage"], 638)
        self.assertEqual(result["candidates"], [
            {"tiploc": "NEWCSTL", "label": "Newcastle", "coordinate": [54.97, -1.62]},
            {"tiploc": "YORK", "label": "York", "coordinate": None},
            {"tiploc": "DRHM", "label": "DRHM", "coordinate": None},
        ])

    def test_candidates_are_capped_at_120(self):
        payload = {"candidates": [{"tiploc": f"T{index:03d}"} for index in range(200)]}
        _, result = self.run_with(completed(payload))
        self.assertEqual(len(result["candidates"]), 120)

    def test_missing_candidates_become_empty_list(self):
        _, result = self.run_with(completed({"mileage": 1}))
        self.assertEqual(result["candidates"], [])


if __name__ == "__main__":
    unittest.main()
