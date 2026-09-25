"""Verify the public no-auth production MCP via the official SDK."""
import argparse
import asyncio
from datetime import date, datetime, time
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def verify(args):
    url = "https://rail.mikegtn.net/mcp"
    async with httpx.AsyncClient(timeout=200, follow_redirects=False, trust_env=False) as http:
        async with streamable_http_client(url, http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                listed = await session.list_tools()
                assert len(listed.tools) == 12
                for tool in listed.tools:
                    raw = tool.model_dump(by_alias=True)
                    assert raw.get("securitySchemes") == [{"type": "noauth"}], (
                        f"{tool.name} missing top-level noauth security scheme"
                    )
                    assert (raw.get("_meta") or {}).get("securitySchemes") == [{"type": "noauth"}], (
                        f"{tool.name} missing noauth compatibility mirror"
                    )
                    assert tool.outputSchema, f"{tool.name} missing output schema"
                print(json.dumps({
                    "protocol": initialized.protocolVersion,
                    "tools": [t.name for t in listed.tools],
                    "authentication": "none",
                }), flush=True)
                if args.protocol_only:
                    return

                report = {"date": args.date, "calls": []}

                async def call(name, parameters):
                    result = await session.call_tool(name, parameters)
                    data = result.structuredContent
                    report["calls"].append({"tool": name, "parameters": parameters, "response": data})
                    if args.output:
                        Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
                    assert not result.isError, f"{name} failed: {result.content}"
                    assert isinstance(data, dict), f"{name} returned no structured result"
                    assert "requestEvidence" not in data and "sourceRequestEvidence" not in data
                    print(json.dumps({"tool": name, "ok": True}), flush=True)
                    return data

                journeys = await call("findJourneys", {
                    "origin": "ABD",
                    "destination": "PLY",
                    "time_from": datetime.combine(
                        date.fromisoformat(args.date), time(), ZoneInfo("Europe/London")
                    ).isoformat(),
                    "minutes": 1439,
                    "interchanges": ["EDB", "NCL", "YRK"],
                })
                assert journeys["itineraries"], "No Aberdeen to Plymouth itinerary in the bounded search"
                chosen = journeys["itineraries"][0]
                legs = [
                    {
                        "unique_identity": leg["uniqueIdentity"],
                        "origin": leg["origin"],
                        "destination": leg["destination"],
                    }
                    for leg in chosen["legs"]
                ]

                for leg in legs:
                    identity = leg["unique_identity"]
                    details = await call("getServiceDetails", {"unique_identity": identity})
                    assert details["scheduleMetadata"]["uniqueIdentity"] == identity
                    await call("getTrainLocation", {"unique_identity": identity})
                    await call("getRouteDetails", {"unique_identity": identity})

                route = await call("getJourneyRoute", {"legs": legs})
                snapshot = await call(
                    "getRailMapSnapshot",
                    {"map_id": route["mapUrl"].rsplit("/", 1)[-1]},
                )

                async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as public:
                    page = await public.get(route["mapUrl"])
                    png = await public.get(snapshot["mapImageUrl"])
                    assert page.status_code == 200 and "leaflet" in page.text.lower()
                    assert png.status_code == 200 and png.content.startswith(b"\x89PNG\r\n\x1a\n")

                print(json.dumps({
                    "journey": chosen,
                    "mapUrl": route["mapUrl"],
                    "mapImageUrl": snapshot["mapImageUrl"],
                    "pngBytes": len(png.content),
                }), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol-only", action="store_true")
    parser.add_argument("--date", default="2026-09-19")
    parser.add_argument("--output")
    args = parser.parse_args()
    date.fromisoformat(args.date)
    asyncio.run(verify(args))


if __name__ == "__main__":
    main()
