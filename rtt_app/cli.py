"""Command-line interface for exploring the RTT API."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .client import RTTClient, RTTError, RTTResponse
from .departures import DepartureBoard, format_board, next_departures


def load_dotenv(path: Path = Path(".env")) -> None:
    """Load a small, conventional KEY=VALUE .env file without dependencies."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _add_common_query_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--detailed", action="store_true")
    parser.add_argument(
        "--generic", action="store_true", help="Use the namespace-generic endpoint"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rtt", description="Explore the Realtime Trains Next Generation API"
    )
    parser.add_argument("--token", help="RTT token (prefer RTT_TOKEN in .env)")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--api-version", default=None)
    parser.add_argument(
        "--token-type", choices=("auto", "access", "refresh"), default=None
    )
    parser.add_argument("--show-rate-limits", action="store_true")

    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("info", help="Show API version and token entitlements")

    next_parser = commands.add_parser(
        "next", help="Show the next departures for a station name"
    )
    next_parser.add_argument("station", help="Station name or CRS code")
    next_parser.add_argument("--count", type=int, default=5)
    next_parser.add_argument("--minutes", type=int, default=180)

    location = commands.add_parser("location", help="List services at a location")
    location.add_argument("code", help="CRS or TIPLOC, for example WAT or CLPHMJN")
    location.add_argument("--from", dest="time_from", help="ISO-8601 start time")
    location.add_argument("--to", dest="time_to", help="ISO-8601 end time")
    location.add_argument("--minutes", type=int, help="Query window in minutes")
    location.add_argument("--filter-from")
    location.add_argument("--filter-to")
    location.add_argument("--stp-filter", help="Combination of W, V, S and C")
    _add_common_query_arguments(location)

    service = commands.add_parser("service", help="Show one train service")
    identity_group = service.add_mutually_exclusive_group(required=True)
    identity_group.add_argument("--unique-id")
    identity_group.add_argument("--identity")
    service.add_argument("--date", dest="departure_date")
    service.add_argument("--namespace", default="gb-nr")
    _add_common_query_arguments(service)

    commands.add_parser("stops", help="List passenger stops")
    commands.add_parser("locations", help="List ungrouped locations")

    allocations_service = commands.add_parser(
        "allocations-service", help="List allocations for an operator and date"
    )
    allocations_service.add_argument("date")
    allocations_service.add_argument("toc")

    allocations_class = commands.add_parser(
        "allocations-class", help="List allocations for a stock class and date"
    )
    allocations_class.add_argument("date")
    allocations_class.add_argument("rolling_stock_class")

    raw = commands.add_parser("raw", help="GET an API path with key=value params")
    raw.add_argument("path")
    raw.add_argument("params", nargs="*")
    return parser


def _raw_params(items: list[str]) -> dict[str, str]:
    params: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"Raw parameter must be key=value: {item}")
        key, value = item.split("=", 1)
        params[key] = value
    return params


def run(args: argparse.Namespace, client: RTTClient) -> RTTResponse | DepartureBoard:
    if args.command == "info":
        return client.info()
    if args.command == "next":
        if not 1 <= args.count <= 20:
            raise ValueError("--count must be between 1 and 20")
        if not 1 <= args.minutes <= 1439:
            raise ValueError("--minutes must be between 1 and 1439")
        return next_departures(
            client, args.station, limit=args.count, minutes=args.minutes
        )
    if args.command == "location":
        if args.time_to and args.minutes is not None:
            raise ValueError("--to and --minutes cannot be used together")
        return client.location(
            args.code,
            time_from=args.time_from,
            time_to=args.time_to,
            time_window=args.minutes,
            filter_from=args.filter_from,
            filter_to=args.filter_to,
            detailed=args.detailed,
            network_rail=not args.generic,
            stp_filter=args.stp_filter,
        )
    if args.command == "service":
        if args.identity and not args.departure_date:
            raise ValueError("--date is required with --identity")
        return client.service(
            unique_identity=args.unique_id,
            identity=args.identity,
            departure_date=args.departure_date,
            namespace=args.namespace,
            detailed=args.detailed,
            network_rail=not args.generic,
        )
    if args.command == "stops":
        return client.stops()
    if args.command == "locations":
        return client.locations()
    if args.command == "allocations-service":
        return client.allocations_by_service(args.date, args.toc)
    if args.command == "allocations-class":
        return client.allocations_by_class(args.date, args.rolling_stock_class)
    if args.command == "raw":
        return client.request(args.path, _raw_params(args.params))
    raise AssertionError("unknown command")


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    token = args.token or os.environ.get("RTT_TOKEN")
    if not token:
        parser.error("Set RTT_TOKEN in .env or pass --token")
    client = RTTClient(
        token,
        base_url=args.base_url
        or os.environ.get("RTT_BASE_URL", "https://data.rtt.io"),
        api_version=args.api_version or os.environ.get("RTT_API_VERSION"),
        token_type=args.token_type or os.environ.get("RTT_TOKEN_TYPE", "auto"),
    )
    try:
        response = run(args, client)
    except (RTTError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if isinstance(response, DepartureBoard):
        print(format_board(response))
    else:
        print(json.dumps(response.data, indent=2, ensure_ascii=False))
    if (
        args.show_rate_limits
        and isinstance(response, RTTResponse)
        and response.rate_limits
    ):
        print("\nRate limits:", file=sys.stderr)
        for key, value in sorted(response.rate_limits.items()):
            print(f"  {key}: {value}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
