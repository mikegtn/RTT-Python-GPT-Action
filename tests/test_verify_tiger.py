"""Exercise verifier request routing without any real credentials or network."""
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from deploy.verify_tiger import main, safe_api_error


class VerificationTests(unittest.TestCase):
    def test_rtt_crs_and_tiger_tiploc_are_separate(self):
        payloads = [
            {'paths': {'/v1/tiger/service': {'get': {'operationId': 'getTigerServiceDetails'}}}},
            {'result': {'services': [{'scheduleMetadata': {'uniqueIdentity': 'gb-nr:G01153:2026-09-18'}}]}},
            {'ok': True, 'result': {'tiger': {'uid': 'G01153', 'station': 'PADTON', 'coaches': [{}], 'totalCoaches': 5}}},
        ]
        opener = Mock()
        opener.open.side_effect = [io.BytesIO(json.dumps(p).encode()) for p in payloads]
        with patch('sys.argv', ['verify_tiger.py', '--station', 'PAD', '--tiger-station', 'PADTON']), \
             patch.dict('os.environ', {'ACTION_API_KEY': 'synthetic-action-key'}, clear=True), \
             patch('deploy.verify_tiger.Path.is_file', return_value=False), \
             patch('deploy.verify_tiger.build_opener', return_value=opener), redirect_stdout(io.StringIO()) as out:
            main()
        urls = [call.args[0].full_url for call in opener.open.call_args_list]
        self.assertIn('/v1/services?station=PAD&', urls[1])
        self.assertIn('station=PAD&', urls[2])
        self.assertIn('tiploc=PADTON', urls[2])
        self.assertNotIn('synthetic-action-key', out.getvalue())
        self.assertIn('"tigerStation": "PADTON"', out.getvalue())


class ErrorDiagnosticsTests(unittest.TestCase):
    def test_api_error_is_reported_without_credentials(self):
        from urllib.error import HTTPError
        body = json.dumps({'error': 'Invalid mapping; key=secret-one token=secret-two'}).encode()
        error = HTTPError('https://rail.example', 400, 'Bad Request', {}, io.BytesIO(body))
        detail = safe_api_error(error, {'TIGER_API_KEY': 'secret-one', 'RTT_TOKEN': 'secret-two'})
        self.assertEqual(detail, 'Invalid mapping; key=[REDACTED] token=[REDACTED]')

    def test_unstructured_response_is_never_printed(self):
        from urllib.error import HTTPError
        for body in [b'secret-one', b'[]', b'{"error": {"key": "secret-one"}}']:
            error = HTTPError('https://rail.example', 400, 'Bad Request', {}, io.BytesIO(body))
            self.assertEqual(safe_api_error(error, {}), 'No structured API error was returned.')


class MixedStationVerifierTests(unittest.TestCase):
    def test_skips_low_level_and_reports_mainline_without_coach_data(self):
        from urllib.error import HTTPError
        payloads = [
            {'paths': {'/v1/tiger/service': {'get': {'operationId': 'getTigerServiceDetails'}}}},
            {'result': {'services': [
                {'scheduleMetadata': {'uniqueIdentity': 'gb-nr:C38478:2026-09-18'}},
                {'scheduleMetadata': {'uniqueIdentity': 'gb-nr:W35225:2026-09-18'}},
            ]}},
        ]
        mismatch = HTTPError('https://rail.example', 400, 'Bad Request', {}, io.BytesIO(
            b'{"error":"tiploc does not match the requested RTT station"}'))
        success = {'ok': True, 'result': {'tiger': {'uid': 'W35225', 'station': 'PADTON',
                   'coaches': [], 'dateVerified': True}, 'coachEnrichmentApplied': False}}
        opener = Mock()
        opener.open.side_effect = [io.BytesIO(json.dumps(p).encode()) for p in payloads] + [
            mismatch, io.BytesIO(json.dumps(success).encode())]
        with patch('sys.argv', ['verify_tiger.py', '--station', 'PAD', '--tiger-station', 'PADTON']), \
             patch.dict('os.environ', {'ACTION_API_KEY': 'synthetic-action-key'}, clear=True), \
             patch('deploy.verify_tiger.Path.is_file', return_value=False), \
             patch('deploy.verify_tiger.build_opener', return_value=opener), redirect_stdout(io.StringIO()) as out:
            main()
        result = json.loads(out.getvalue().splitlines()[-1])
        self.assertEqual(result['uid'], 'W35225')
        self.assertEqual(result['skippedOtherTiploc'], 1)
        self.assertFalse(result['coachDataAvailable'])
        self.assertTrue(result['dateVerified'])
