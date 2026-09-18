"""Exercise verifier request routing without any real credentials or network."""
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from deploy.verify_tiger import main


class VerificationTests(unittest.TestCase):
    def test_rtt_crs_and_tiger_tiploc_are_separate(self):
        payloads = [
            {'paths': {'/v1/tiger/service': {'get': {'operationId': 'getTigerServiceDetails'}}}},
            {'result': {'services': [{'scheduleMetadata': {'uniqueIdentity': 'gb-nr:G01153:2026-09-18'}}]}},
            {'ok': True, 'result': {'tiger': {'station': 'PADTON', 'coaches': [{}], 'totalCoaches': 5}}},
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
