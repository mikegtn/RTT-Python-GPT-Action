"""Verify production MCP via the official SDK. Credentials stay on the server."""
import argparse
import asyncio
from datetime import date, datetime, time
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def verify(args, key=None):
    config = {}
    for line in (Path(args.env_file).read_text().splitlines() if key is None else []):
        if '=' in line and not line.lstrip().startswith('#'):
            env_name, value = line.split('=', 1)
            config[env_name.strip()] = value.strip().strip('"').strip("'")
    key = key or config.get('MCP_API_KEY') or config['ACTION_API_KEY']
    url = 'https://rail.mikegtn.net/mcp'
    async with httpx.AsyncClient(timeout=200, follow_redirects=False, trust_env=False) as unauth:
        response = await unauth.post(url, json={})
        assert response.status_code == 401, 'Unauthenticated request was not rejected'
    async with httpx.AsyncClient(headers={'Authorization': 'Bearer ' + key}, timeout=200,
                                 follow_redirects=False, trust_env=False) as http:
        async with streamable_http_client(url, http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                listed = await session.list_tools()
                assert len(listed.tools) == 13
                await session.read_resource('skill://realtime-trains/realtime-trains/SKILL.md')
                knowledge = await session.read_resource('skill://realtime-trains/realtime-trains/references/MOVEBOOK.md')
                assert 'TIGER' in knowledge.contents[0].text
                print(json.dumps({'protocol': initialized.protocolVersion, 'tools': [t.name for t in listed.tools],
                                  'unauthenticatedStatus': response.status_code}), flush=True)
                if args.protocol_only:
                    return
                report = {'date': args.date, 'calls': []}
                async def call(name, parameters):
                    result = await session.call_tool(name, parameters)
                    data = result.structuredContent
                    report['calls'].append({'tool': name, 'parameters': parameters, 'response': data})
                    if args.output:
                        Path(args.output).write_text(json.dumps(report, indent=2), encoding='utf-8')
                    assert not result.isError, f'{name} failed: {data}'
                    assert data and data.get('requestEvidence'), f'{name} missing evidence'
                    print(json.dumps({'tool': name, 'evidence': data['requestEvidence']}), flush=True)
                    return data['result']
                if getattr(args, 'service_progress', False):
                    identity = 'gb-nr:G01162:2026-09-19'
                    details = await call('getServiceDetails', {'unique_identity': identity})
                    assert details['scheduleMetadata']['uniqueIdentity'] == identity
                    for clock, state in [('14:00:00', 'not_started'), ('20:30:00', 'at_station'),
                                         ('20:35:00', 'between_calls'), ('22:00:00', 'completed')]:
                        progress = await call('getServiceProgress', {
                            'unique_identity': identity, 'as_of': '2026-09-19T' + clock + '+01:00'})
                        assert progress['state'] == state, progress
                        assert progress['uniqueIdentity'] == identity
                        assert report['calls'][-1]['response']['sourceRequestEvidence']
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
                    identity = leg['unique_identity']
                    details = await call('getServiceDetails', {'unique_identity': identity})
                    assert details['scheduleMetadata']['uniqueIdentity'] == identity
                    await call('getTrainLocation', {'unique_identity': identity})
                    await call('getRouteDetails', {'unique_identity': identity})
                route = await call('getJourneyRoute', {'legs': legs})
                snapshot = await call('getRailMapSnapshot', {'map_id': route['mapUrl'].rsplit('/', 1)[-1]})
                # Public artifact requests never receive the bearer key.
                async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as public:
                    page = await public.get(route['mapUrl'])
                    png = await public.get(snapshot['mapImageUrl'])
                    assert page.status_code == 200 and 'leaflet' in page.text.lower()
                    assert png.status_code == 200 and png.content.startswith(b'\x89PNG\r\n\x1a\n')
                print(json.dumps({'journey': chosen, 'mapUrl': route['mapUrl'],
                                  'mapImageUrl': snapshot['mapImageUrl'], 'pngBytes': len(png.content)}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', default='/etc/rtt-action.env')
    parser.add_argument('--protocol-only', action='store_true')
    parser.add_argument('--multi-interchange', action='store_true')
    parser.add_argument('--service-progress', action='store_true')
    parser.add_argument('--date', default='2026-09-19')
    parser.add_argument('--output')
    args = parser.parse_args()
    date.fromisoformat(args.date)
    asyncio.run(verify(args))


if __name__ == '__main__':
    main()
