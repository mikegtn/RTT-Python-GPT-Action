import base64
import importlib.util
from copy import deepcopy
from io import BytesIO
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from rtt_app.mcp_tools import RailWorkflows
from rtt_app.progress_image import ProgressImages, render, TTL, timing_color, RED, TEAL, MUTED
from rtt_app.service_progress import service_progress
from tests.test_service_progress import fixture


@unittest.skipUnless(importlib.util.find_spec('PIL'), 'Install snapshots extra')
class ImageTests(unittest.TestCase):
    def test_exact_minute_threshold_and_unknown_booked_time(self):
        timing = {'scheduleAdvertised': '2026-09-19T20:00:00+01:00'}
        for actual, expected in [('19:59:00', TEAL), ('20:01:00', TEAL), ('20:01:01', RED)]:
            timing['realtimeActual'] = '2026-09-19T' + actual + '+01:00'
            self.assertEqual(timing_color(timing), expected)
        timing['realtimeActual'] = '2026-09-19T19:01:00Z'
        self.assertEqual(timing_color(timing), TEAL)
        self.assertEqual(timing_color({'realtimeActual': timing['realtimeActual']}), MUTED)

    def test_completed_segment_uses_arrival_and_current_segment_glows(self):
        from PIL import Image, ImageColor
        data = fixture()
        # Arrival just within the tolerance, departure late: completed inbound
        # section stays teal while the currently occupied outbound section is red.
        data['calls'][1]['temporalData']['arrival']['scheduleAdvertised'] = '2026-09-19T20:41:00'
        png = render(data, self.progress(data, '20:44:00'))[0]
        with Image.open(BytesIO(png)) as image:
            self.assertEqual(image.getpixel((84, 392)), ImageColor.getrgb(TEAL))
            self.assertEqual(image.getpixel((84, 440)), ImageColor.getrgb(RED))
            self.assertNotEqual(image.getpixel((101, 440)), ImageColor.getrgb('#f5f8fa'))
        data['calls'][1]['temporalData']['arrival']['scheduleAdvertised'] = '2026-09-19T20:40:00'
        with Image.open(BytesIO(render(data, self.progress(data, '20:44:00'))[0])) as image:
            self.assertEqual(image.getpixel((84, 392)), ImageColor.getrgb(RED))

    def progress(self, data=None, clock='20:35:00'):
        return service_progress(data or fixture(), 'opaque', '2026-09-19T' + clock + '+01:00')

    def test_four_states_render_and_future_actuals_are_invisible(self):
        from PIL import Image
        data = fixture()
        for clock, phrase in [('20:20:00', 'No actual'), ('20:30:00', 'Reported at Taunton'),
                              ('20:35:00', 'Between Taunton and Tiverton Parkway'), ('22:00:00', 'Completed at Plymouth')]:
            png, size, alt = render(data, self.progress(data, clock))
            self.assertIn(phrase, alt)
            with Image.open(BytesIO(png)) as image:
                self.assertEqual(image.size, size)
                self.assertEqual(image.format, 'PNG')
                image.verify()
        before = render(data, self.progress(data))[0]
        data['calls'][1]['temporalData']['arrival']['realtimeActual'] = '2026-09-19T20:49:00'
        data['calls'][1]['temporalData']['departure']['realtimeActual'] = '2026-09-19T20:50:00'
        data['calls'][1]['temporalData']['departure']['realtimeForecast'] = '2026-09-19T20:36:00'
        self.assertEqual(before, render(data, self.progress(data))[0])

    def test_cancelled_call_shown_but_not_selected(self):
        data = fixture()
        data['calls'][1]['temporalData']['realtimeCallType'] = 'CANCELLED_CALL'
        progress = self.progress(data)
        png, _, alt = render(data, progress)
        self.assertIn('Between Taunton and Plymouth', alt)
        self.assertGreater(len(png), 1000)

    def test_repeated_names_use_exact_call_index(self):
        data = fixture()
        earlier = deepcopy(data['calls'][0])
        earlier['temporalData']['arrival']['realtimeActual'] = '2026-09-19T19:00:00'
        earlier['temporalData']['departure']['realtimeActual'] = '2026-09-19T19:01:00'
        data['calls'].insert(0, earlier)
        progress = self.progress(data)
        self.assertEqual(progress['from']['callIndex'], 1)
        render(data, progress)

    def test_storage_public_read_expiry_limits_and_invalid_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ProgressImages(directory)
            with patch('rtt_app.progress_image.MAX_IMAGES', 2):
                old = store.create(fixture(), self.progress())
                store.create(fixture(), self.progress())
                latest = store.create(fixture(), self.progress())
            self.assertIsNone(store.read(old['schematicId']))
            self.assertTrue(store.read(latest['schematicId']).startswith(b'\x89PNG'))
            self.assertIsNone(store.read('../oauth.sqlite3'))
            self.assertIsNone(store.read('x' * 32))
            target = Path(directory) / (latest['schematicId'] + '.png')
            os.utime(target, (time.time()-TTL-1, time.time()-TTL-1))
            self.assertIsNone(store.read(latest['schematicId']))
            self.assertEqual(len(list(Path(directory).glob('*.png'))), 2)

    def test_oversized_service_is_rejected(self):
        data = fixture()
        data['calls'] *= 41
        with self.assertRaises(ValueError):
            render(data, self.progress())


class ImageWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_opt_in_only_and_graceful_image_failure(self):
        async def backend(path, args):
            return {'ok': True, 'result': fixture(), 'requestEvidence': {'requestId': 'source'}}
        args = {'unique_identity': 'opaque', 'as_of': '2026-09-19T20:35:00+01:00'}
        with tempfile.TemporaryDirectory() as directory:
            store = ProgressImages(directory)
            workflows = RailWorkflows(backend, store)
            result = await workflows.call('getServiceProgress', args)
            self.assertNotIn('imageUrl', result['result'])
            self.assertEqual(list(Path(directory).iterdir()), [])
            with patch.object(store, 'create', side_effect=OSError('private path must not leak')):
                failed = await workflows.call('getServiceProgress', {**args, 'include_image': True})
            self.assertTrue(failed['ok'])
            self.assertEqual(failed['result']['state'], 'between_calls')
            self.assertIn('imageError', failed['result'])
            self.assertNotIn('private path', str(failed))
            self.assertEqual(failed['sourceRequestEvidence'], [{'requestId': 'source'}])


@unittest.skipUnless(importlib.util.find_spec('PIL') and importlib.util.find_spec('mcp'), 'Install mcp and snapshots extras')
class ImageTransportTests(unittest.TestCase):
    def test_public_mcp_native_image_matches_public_png_without_diagnostics(self):
        from starlette.testclient import TestClient
        from rtt_app.mcp_server import create_server, create_http_app
        async def backend(path, args):
            return {'ok': True, 'result': fixture(), 'requestEvidence': {'requestId': 'exact-source'}}
        with tempfile.TemporaryDirectory() as directory:
            store = ProgressImages(directory)
            app = create_http_app(create_server(backend, progress_images=store), progress_images=store)
            with TestClient(app) as client:
                params = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
                    'name': 'getServiceSchematic', 'arguments': {'unique_identity': 'opaque',
                    'as_of': '2026-09-19T20:35:00+01:00'}}}
                self.assertEqual(list(Path(directory).iterdir()), [])
                response = client.post('/mcp', json=params, headers={'Accept': 'application/json, text/event-stream'}).json()['result']
                self.assertFalse(response['isError'])
                body = response['structuredContent']
                self.assertNotIn('requestEvidence', body)
                self.assertNotIn('sourceRequestEvidence', body)
                native = next(c for c in response['content'] if c['type'] == 'image')
                fallback = response['structuredContent']['imageLinkMarkdown']
                self.assertTrue(fallback.startswith('[Open schematic](https://'))
                self.assertTrue(any(fallback in c.get('text', '') and 'fallback link' in c.get('text', '')
                                    for c in response['content']))
                public = client.get('/mcp/progress/' + body['schematicId'] + '.png')
                self.assertEqual(public.status_code, 200)
                self.assertEqual(public.headers['content-type'], 'image/png')
                self.assertEqual(public.content, base64.b64decode(native['data']))
                self.assertEqual(client.get('/mcp/progress/invalid.png').status_code, 404)
                self.assertEqual(client.get('/mcp/progress/' + 'f'*32 + '.png').status_code, 404)
