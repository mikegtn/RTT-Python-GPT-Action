"""Compatibility and transport isolation for direct MCP railway operations."""
import asyncio
import json
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from rtt_app.action_api import ActionApplication
from rtt_app.railway_service import RailwayService, create_railway_service
from rtt_app.mcp_tools import RailWorkflows
from tests.test_action_api import FakeTools, FakeRouteEngine
from tests.test_mcp import service


MCP_AVAILABLE = importlib.util.find_spec('mcp') is not None
if MCP_AVAILABLE:
    from rtt_app.mcp_server import DirectBackend, create_http_app, create_server
    from starlette.testclient import TestClient


@unittest.skipUnless(MCP_AVAILABLE, 'Install the mcp extra')
class DirectBackendTests(unittest.TestCase):
    def test_operations_match_legacy_contract_without_http(self):
        legacy = ActionApplication(FakeTools(), api_key='test', base_url='https://example.com')
        core = RailwayService(FakeTools(), base_url='https://example.com')
        cases = [('/v1/service', {'unique_identity': 'gb-nr:G01162:2026-09-19'}),
                 ('/v1/departures', {'station': 'TAU', 'count': 2}),
                 ('/v1/services', {'station': 'TAU', 'movement': 'departures'}),
                 ('/v1/info', {}), ('/v1/departures', {'count': 0}),
                 ('/v1/tiger/service', {'station': 'TAU', 'uid': 'G01162'}),
                 ('/v1/route', {'origin': 'TAU', 'destination': 'TVP'})]
        with patch('httpx.AsyncClient', side_effect=AssertionError('HTTP adapter called')):
            for path, params in cases:
                with self.subTest(path=path, params=params):
                    old = legacy.dispatch('GET', path, {k: [str(v)] for k,v in params.items()},
                                          {'Authorization': 'Bearer test'}).body
                    new = asyncio.run(DirectBackend(core)(path, params))
                    old_ev, new_ev = old.pop('requestEvidence'), new.pop('requestEvidence')
                    self.assertEqual(old, new)
                    self.assertEqual(old_ev['operation'], new_ev['operation'])
                    self.assertTrue(new_ev['requestId'])
                    self.assertTrue(new_ev['completedAt'])
        self.assertEqual(core.get_usage()['totalRequests'], len(cases))
        self.assertEqual(legacy.get_usage()['totalRequests'], 0)

    def test_progress_uses_direct_service_and_retains_source_evidence(self):
        data = service('gb-nr:G01162:2026-09-19', 'TAU', 'TVP', '20:17', '20:28')
        data['calls'][0]['temporalData']['departure']['realtimeActual'] = '2026-09-19T20:30:30+01:00'
        data['calls'][1]['temporalData']['arrival']['realtimeActual'] = '2026-09-19T20:41:45+01:00'
        with patch.object(FakeTools, 'get_service_details', return_value=data):
            core = RailwayService(FakeTools(), base_url='https://example.com/mcp/assets')
            result = asyncio.run(RailWorkflows(DirectBackend(core)).call('getServiceProgress', {
                'unique_identity': 'gb-nr:G01162:2026-09-19', 'as_of': '2026-09-19T20:35:00+01:00'}))
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['result']['state'], 'between_calls')
        self.assertEqual(result['result']['uniqueIdentity'], 'gb-nr:G01162:2026-09-19')
        self.assertEqual(result['sourceRequestEvidence'][0]['operation'], 'getServiceDetails')
        self.assertEqual(result['requestEvidence']['operation'], 'getServiceProgress')

    def test_mcp_owns_persistent_maps_and_counters(self):
        with TemporaryDirectory() as tmp:
            usage = str(Path(tmp)/'usage.json')
            core = RailwayService(FakeTools(), base_url='https://example.com/mcp/assets',
                                  map_dir=tmp, usage_file=usage, route_engine=FakeRouteEngine())
            result = asyncio.run(DirectBackend(core)('/v1/route', {'origin': 'BRI', 'destination': 'PAD'}))
            self.assertTrue(result['ok'], result)
            url = result['result']['mapUrl']
            self.assertTrue(url.startswith('https://example.com/mcp/assets/maps/'))
            app = create_http_app(create_server(DirectBackend(core)), railway_service=core)
            with TestClient(app) as client:
                page = client.get(url.replace('https://example.com',''))
                self.assertEqual(page.status_code, 200)
                self.assertIn('leaflet', page.text)
                self.assertEqual(client.get('/mcp/assets/maps/invalid').status_code, 404)
                from rtt_app.tiger_icons import ICONS
                for name in ICONS:
                    icon = client.get('/mcp/assets/icons/coach-' + name + '.svg')
                    self.assertEqual(icon.status_code, 200)
                    self.assertEqual(icon.text, ICONS[name])
                self.assertEqual(client.get('/health').json()['authentication'], 'none')
                self.assertEqual(client.get('/health').json()['backend'], 'direct')
            self.assertEqual(json.loads(Path(usage).read_text())['totalRequests'], 1)
            reloaded = RailwayService(FakeTools(), base_url='https://example.com', usage_file=usage)
            self.assertEqual(reloaded.get_usage()['totalRequests'], 1)

    def test_asset_base_does_not_replace_upstream_urls(self):
        with patch('rtt_app.railway_service.load_dotenv'), patch.dict('os.environ', {
                'RTT_TOKEN': 'test', 'RTT_BASE_URL': 'https://rtt.example',
                'TIGER_API_KEY': 'test', 'TIGER_BASE_URL': 'https://tiger.example'}, clear=True), \
                patch('rtt_app.railway_service.RTTClient') as rtt, \
                patch('rtt_app.railway_service.TigerClient') as tiger:
            core = create_railway_service(base_url='https://maps.example/mcp/assets')
            self.assertEqual(core.base_url, 'https://maps.example/mcp/assets')
            self.assertEqual(rtt.call_args.kwargs['base_url'], 'https://rtt.example')
            self.assertEqual(tiger.call_args.kwargs['base_url'], 'https://tiger.example')
