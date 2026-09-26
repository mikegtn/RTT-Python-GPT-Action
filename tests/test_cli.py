import contextlib
import io
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from rtt_app import cli
from rtt_app.client import RTTError, RTTResponse


class LoadDotenvTests(unittest.TestCase):
    def load(self, text, env=None):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(text, encoding="utf-8")
            with patch.dict(os.environ, env or {}, clear=True):
                cli.load_dotenv(path)
                return dict(os.environ)

    def test_missing_file_is_ignored(self):
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            cli.load_dotenv(Path(tmp) / "absent.env")
            self.assertEqual(dict(os.environ), {})

    def test_parses_plain_quoted_and_padded_values(self):
        env = self.load('A=one\n  B = two  \nC="three"\nD=\'four\'\n')
        self.assertEqual([env[key] for key in "ABCD"], ["one", "two", "three", "four"])

    def test_skips_comments_blank_lines_and_lines_without_equals(self):
        env = self.load("# COMMENT=1\n\n   \nNOT_A_PAIR\nKEPT=yes\n")
        self.assertEqual(env, {"KEPT": "yes"})

    def test_value_may_contain_equals_signs(self):
        self.assertEqual(self.load("TOKEN=abc==\n")["TOKEN"], "abc==")

    def test_existing_environment_wins(self):
        env = self.load("RTT_TOKEN=from-file\n", env={"RTT_TOKEN": "from-env"})
        self.assertEqual(env["RTT_TOKEN"], "from-env")


class RawParamsTests(unittest.TestCase):
    def test_parses_key_value_pairs(self):
        self.assertEqual(cli._raw_params(["code=WAT", "timeWindow=30", "q=a=b"]),
                         {"code": "WAT", "timeWindow": "30", "q": "a=b"})

    def test_empty_list_gives_empty_mapping(self):
        self.assertEqual(cli._raw_params([]), {})

    def test_rejects_item_without_equals(self):
        with self.assertRaisesRegex(ValueError, "key=value: WAT"):
            cli._raw_params(["WAT"])


class ParserTests(unittest.TestCase):
    def parse(self, *argv):
        return cli.build_parser().parse_args(argv)

    def assert_parse_error(self, *argv):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            self.parse(*argv)
        self.assertEqual(caught.exception.code, 2)

    def test_command_is_required(self):
        self.assert_parse_error()

    def test_next_defaults(self):
        args = self.parse("next", "Clapham Junction")
        self.assertEqual((args.station, args.count, args.minutes), ("Clapham Junction", 5, 180))

    def test_location_options_map_to_destinations(self):
        args = self.parse("location", "CLJ", "--from", "2026-08-13T14:00:00+01:00",
                          "--minutes", "120", "--filter-to", "WAT", "--detailed", "--generic")
        self.assertEqual((args.code, args.time_from, args.minutes, args.filter_to),
                         ("CLJ", "2026-08-13T14:00:00+01:00", 120, "WAT"))
        self.assertTrue(args.detailed and args.generic)

    def test_service_requires_exactly_one_identifier(self):
        self.assert_parse_error("service")
        self.assert_parse_error("service", "--unique-id", "A", "--identity", "B")

    def test_token_type_is_restricted(self):
        self.assert_parse_error("--token-type", "bogus", "info")

    def test_global_options_precede_command(self):
        args = self.parse("--token", "t", "--show-rate-limits", "info")
        self.assertEqual((args.token, args.show_rate_limits, args.command), ("t", True, "info"))


class RunTests(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()

    def run_cli(self, *argv):
        return cli.run(cli.build_parser().parse_args(argv), self.client)

    def test_info_delegates_to_client(self):
        self.assertIs(self.run_cli("info"), self.client.info.return_value)

    def test_next_validates_count_and_minutes(self):
        for argv in (("--count", "0"), ("--count", "21"), ("--minutes", "0"), ("--minutes", "1440")):
            with self.subTest(argv=argv), self.assertRaisesRegex(ValueError, "must be between"):
                self.run_cli("next", "WAT", *argv)

    def test_next_passes_limits_to_departures(self):
        with patch.object(cli, "next_departures") as next_departures:
            result = self.run_cli("next", "WAT", "--count", "3", "--minutes", "90")
        next_departures.assert_called_once_with(self.client, "WAT", limit=3, minutes=90)
        self.assertIs(result, next_departures.return_value)

    def test_location_forwards_options(self):
        self.run_cli("location", "WAT", "--to", "2026-08-13T15:00:00+01:00", "--filter-from", "CLJ",
                     "--stp-filter", "WV", "--generic")
        self.client.location.assert_called_once_with(
            "WAT", time_from=None, time_to="2026-08-13T15:00:00+01:00", time_window=None,
            filter_from="CLJ", filter_to=None, detailed=False, network_rail=False, stp_filter="WV")

    def test_location_rejects_to_with_minutes(self):
        with self.assertRaisesRegex(ValueError, "cannot be used together"):
            self.run_cli("location", "WAT", "--to", "2026-08-13T15:00:00+01:00", "--minutes", "30")
        self.client.location.assert_not_called()

    def test_location_accepts_zero_minutes_without_to(self):
        self.run_cli("location", "WAT", "--minutes", "0")
        self.assertEqual(self.client.location.call_args.kwargs["time_window"], 0)

    def test_service_identity_requires_date(self):
        with self.assertRaisesRegex(ValueError, "--date is required"):
            self.run_cli("service", "--identity", "L01525")
        self.client.service.assert_not_called()

    def test_service_forwards_unique_id_and_defaults_to_network_rail(self):
        self.run_cli("service", "--unique-id", "L01525:2026-08-13")
        self.client.service.assert_called_once_with(
            unique_identity="L01525:2026-08-13", identity=None, departure_date=None,
            namespace="gb-nr", detailed=False, network_rail=True)

    def test_reference_and_allocation_commands_delegate(self):
        self.run_cli("stops")
        self.run_cli("locations")
        self.run_cli("allocations-service", "2026-08-13", "SW")
        self.run_cli("allocations-class", "2026-08-13", "444")
        self.client.stops.assert_called_once_with()
        self.client.locations.assert_called_once_with()
        self.client.allocations_by_service.assert_called_once_with("2026-08-13", "SW")
        self.client.allocations_by_class.assert_called_once_with("2026-08-13", "444")

    def test_raw_parses_params(self):
        self.run_cli("raw", "/gb-nr/location", "code=WAT", "timeWindow=30")
        self.client.request.assert_called_once_with("/gb-nr/location", {"code": "WAT", "timeWindow": "30"})

    def test_raw_rejects_malformed_param(self):
        with self.assertRaisesRegex(ValueError, "key=value"):
            self.run_cli("raw", "/gb-nr/location", "oops")


class MainTests(unittest.TestCase):
    def invoke(self, argv, env=None, response=None, error=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        fake_run = MagicMock(return_value=response, side_effect=error)
        with patch.object(cli, "load_dotenv"), patch.object(cli, "run", fake_run), \
                patch.object(cli, "RTTClient") as client_class, \
                patch.dict(os.environ, env or {}, clear=True), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = cli.main(argv)
            except SystemExit as exit_:
                code = exit_.code
        return code, stdout.getvalue(), stderr.getvalue(), client_class

    def test_missing_token_is_a_usage_error(self):
        code, _, stderr, client_class = self.invoke(["info"])
        self.assertEqual(code, 2)
        self.assertIn("Set RTT_TOKEN", stderr)
        client_class.assert_not_called()

    def test_token_option_beats_environment_and_env_supplies_defaults(self):
        response = RTTResponse(data={}, status=200, rate_limits={})
        env = {"RTT_TOKEN": "env-token", "RTT_API_VERSION": "2026-04-09", "RTT_TOKEN_TYPE": "refresh"}
        _, _, _, client_class = self.invoke(["--token", "cli-token", "info"], env=env, response=response)
        client_class.assert_called_once_with(
            "cli-token", base_url="https://data.rtt.io", api_version="2026-04-09", token_type="refresh")

    def test_command_line_overrides_environment_settings(self):
        response = RTTResponse(data={}, status=200, rate_limits={})
        env = {"RTT_TOKEN": "t", "RTT_BASE_URL": "https://env.example", "RTT_TOKEN_TYPE": "refresh"}
        _, _, _, client_class = self.invoke(
            ["--base-url", "https://cli.example", "--token-type", "access", "info"], env=env, response=response)
        self.assertEqual(client_class.call_args.kwargs["base_url"], "https://cli.example")
        self.assertEqual(client_class.call_args.kwargs["token_type"], "access")

    def test_prints_response_data_as_unescaped_json(self):
        response = RTTResponse(data={"station": "Nürnberg"}, status=200, rate_limits={"limit": "10"})
        code, stdout, stderr, _ = self.invoke(["info"], env={"RTT_TOKEN": "t"}, response=response)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout), {"station": "Nürnberg"})
        self.assertIn("Nürnberg", stdout)
        self.assertEqual(stderr, "")

    def test_rate_limits_go_to_stderr_only_when_requested(self):
        response = RTTResponse(data={}, status=200, rate_limits={"remaining": "9", "limit": "10"})
        _, stdout, stderr, _ = self.invoke(["--show-rate-limits", "info"], env={"RTT_TOKEN": "t"}, response=response)
        self.assertEqual(json.loads(stdout), {})
        self.assertLess(stderr.index("limit: 10"), stderr.index("remaining: 9"))
        _, _, quiet, _ = self.invoke(["info"], env={"RTT_TOKEN": "t"}, response=response)
        self.assertEqual(quiet, "")

    def test_known_errors_return_one_without_traceback(self):
        for error in (RTTError("Bad token", status=401), ValueError("--count must be between 1 and 20")):
            with self.subTest(error=type(error).__name__):
                code, stdout, stderr, _ = self.invoke(["info"], env={"RTT_TOKEN": "t"}, error=error)
                self.assertEqual(code, 1)
                self.assertEqual(stdout, "")
                self.assertEqual(stderr, f"Error: {error}\n")

    def test_departure_board_is_formatted_not_dumped_as_json(self):
        from rtt_app.departures import DepartureBoard
        board = DepartureBoard(station="Waterloo", station_code="WAT", departures=[])
        with patch.object(cli, "format_board", return_value="BOARD TEXT") as format_board:
            code, stdout, _, _ = self.invoke(["next", "WAT"], env={"RTT_TOKEN": "t"}, response=board)
        self.assertEqual((code, stdout), (0, "BOARD TEXT\n"))
        format_board.assert_called_once_with(board)


if __name__ == "__main__":
    unittest.main()
