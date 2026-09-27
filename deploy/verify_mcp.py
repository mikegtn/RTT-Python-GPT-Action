"""Verify the public no-auth production MCP via the official SDK."""
import argparse
import asyncio
import base64
import hashlib
from datetime import date, datetime, time
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from jsonschema import validate
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def verify(args):
    url = "https://rail.mikegtn.net/mcp"
    async with httpx.AsyncClient(timeout=200, follow_redirects=False, trust_env=False) as http:
        async with streamable_http_client(url, http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                listed = await session.list_tools()
                assert len(listed.tools) == 14
                assert initialized.serverInfo.name == "trainbrain"
                assert not initialized.capabilities.resources
                schemas = {tool.name: tool.outputSchema for tool in listed.tools}
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
                        Path(args.output).write_text(json.dumps(report, indent=2), encoding='utf-8')
                    assert not result.isError, f'{name} failed: {result.content}'
                    assert isinstance(data, dict), f'{name} missing structured result'
                    assert 'requestEvidence' not in data and 'sourceRequestEvidence' not in data
                    validate(data, schemas[name])
                    if parameters.get('include_image') or name == 'getServiceSchematic':
                        value = data
                        assert 'imageError' not in value, value
                        native = [c for c in result.content if c.type == 'image']
                        assert len(native) == 1 and native[0].mimeType == 'image/png'
                        png = base64.b64decode(native[0].data, validate=True)
                        assert png.startswith(b'\x89PNG\r\n\x1a\n')
                        async with httpx.AsyncClient(timeout=30, trust_env=False) as public:
                            fetched = await public.get(value['imageUrl'])
                        assert fetched.status_code == 200 and fetched.headers['content-type'].startswith('image/png')
                        assert fetched.content == png
                        report['calls'][-1]['imageVerification'] = {'publicStatus': fetched.status_code,
                            'nativeImageMatchesPublic': True, 'sha256': hashlib.sha256(png).hexdigest(), 'bytes': len(png)}
                        if args.output:
                            target = Path(args.output).with_name(Path(args.output).stem + '-' + value['state'] + '.png')
                            target.write_bytes(png)
                            Path(args.output).write_text(json.dumps(report, indent=2), encoding='utf-8')
                    print(json.dumps({'tool': name, 'ok': True}), flush=True)
                    return data
                if getattr(args, 'service_progress', False):
                    identity = 'gb-nr:G01162:2026-09-19'
                    details = await call('getServiceDetails', {'unique_identity': identity})
                    assert details['scheduleMetadata']['uniqueIdentity'] == identity
                    for clock, state in [('14:00:00', 'not_started'), ('20:30:00', 'at_station'),
                                         ('20:35:00', 'between_calls'), ('22:00:00', 'completed')]:
                        progress = await call('getServiceSchematic' if getattr(args, 'progress_image', False) else 'getServiceProgress', {
                            'unique_identity': identity, 'as_of': '2026-09-19T' + clock + '+01:00'})
                        assert progress['state'] == state, progress
                        assert progress['uniqueIdentity'] == identity
                        if state == 'between_calls':
                            assert progress['from']['name'] == 'Taunton'
                            assert progress['from']['actualDeparture'] == '2026-09-19T20:30:30'
                            assert progress['to']['name'] == 'Tiverton Parkway'
                            assert progress['to']['actualArrival'] is None
                            assert progress['latenessMinutes'] == 13
                        elif state == 'at_station':
                            assert progress['at']['name'] == 'Taunton'
                            assert progress['at']['actualDeparture'] is None
                        elif state == 'completed':
                            assert progress['at']['name'] == 'Plymouth'
                    print(json.dumps({'serviceProgressRegression': 'PASS', 'uniqueIdentity': identity}), flush=True)
                    return
                journeys = await call('findJourneys', {'origin': 'ABD', 'destination': 'PLY',
                    'time_from': datetime.combine(date.fromisoformat(args.date), time(8, 20) if getattr(args, 'multi_interchange', False) else time(), ZoneInfo('Europe/London')).isoformat(),
                    'minutes': 10 if getattr(args, 'multi_interchange', False) else 1439,
                    'interchanges': ['EDB', 'BHM'] if getattr(args, 'multi_interchange', False) else ['EDB', 'NCL', 'YRK']})
                assert journeys['itineraries'], 'No Aberdeen to Plymouth itinerary in the bounded search'
                chosen = journeys['itineraries'][0]
                if getattr(args, 'multi_interchange', False):
                    assert len(chosen['legs']) == 3, 'Expected Aberdeen-Edinburgh-Birmingham-Plymouth'
                    assert [c['station'] for c in chosen['connections']] == ['EDB', 'BHM']
                legs = [{'unique_identity': l['uniqueIdentity'], 'origin': l['origin'], 'destination': l['destination']}
                        for l in chosen['legs']]
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
    parser.add_argument('--protocol-only', action='store_true')
    parser.add_argument('--multi-interchange', action='store_true')
    parser.add_argument('--service-progress', action='store_true')
    parser.add_argument('--progress-image', action='store_true', help='With --service-progress, verify native and public PNGs for all four states')
    parser.add_argument('--date', default='2026-09-19')
    parser.add_argument('--output')
    args = parser.parse_args()
    date.fromisoformat(args.date)
    asyncio.run(verify(args))


if __name__ == "__main__":
    main()
