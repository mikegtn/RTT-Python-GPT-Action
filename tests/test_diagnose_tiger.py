import unittest
from deploy.diagnose_tiger import date_evidence, fields


class DiagnosticTests(unittest.TestCase):
    def test_nested_dates_and_times_without_other_values(self):
        payload = {'UID': 'G15496', 'Origin': {'ScheduledDeparture': '2026-09-18T12:00:00+01:00'},
                   'RunDate': '20260918', 'ExpectedTime': '12:05', 'Timestamp': 1789732800,
                   'Other': 'private text', 'CoachList': [{'Time': '12:00'}]}
        result = date_evidence(payload)
        self.assertEqual([v['path'] for v in result], ['$.Origin.ScheduledDeparture', '$.RunDate', '$.ExpectedTime', '$.Timestamp'])
        self.assertNotIn('private text', str(result))

    def test_sensitive_branches_are_omitted(self):
        payload = {'apiKey': '2026-09-18', 'Credentials': {'date': '2026-09-18'}, 'RunDate': None}
        self.assertEqual(date_evidence(payload), [])
        self.assertEqual(fields(payload), {'RunDate': 'NoneType'})

    def test_sampling_is_bounded(self):
        self.assertEqual(len(date_evidence([{'Date': '2026-09-18'}] * 100)), 3)
