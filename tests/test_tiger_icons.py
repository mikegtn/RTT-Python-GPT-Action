import copy
import unittest
from unittest.mock import Mock
from xml.etree import ElementTree

from rtt_app.action_api import ActionApplication
from rtt_app.tiger import normalize_coach_list
from rtt_app.tiger_icons import ICONS, add_coach_icons
from tests.test_tiger import service


class CoachIconTests(unittest.TestCase):
    def test_positions_follow_normalized_orientation_and_keep_raw_evidence(self):
        raw = service()['CoachList']
        original = copy.deepcopy(raw)
        result = add_coach_icons(normalize_coach_list(raw), 'https://rail.example/')
        self.assertEqual([c['position'] for c in result['coaches']],
                         ['front', 'intermediate', 'intermediate', 'intermediate', 'rear'])
        self.assertEqual(result['coaches'][0]['iconUrl'], 'https://rail.example/icons/coach-front.svg')
        self.assertEqual(raw, original)
        raw[0]['LeadingPowerCar'] = False
        raw[-1]['LeadingPowerCar'] = True
        reversed_result = normalize_coach_list(raw)
        self.assertEqual(reversed_result['coaches'][0]['coachLetter'], 'A')
        self.assertEqual(reversed_result['coaches'][0]['position'], 'front')

    def test_unknown_and_single_coach_do_not_mislabel_ends(self):
        raw = service()['CoachList']
        del raw[0]['LeadingPowerCar']
        self.assertEqual({c['position'] for c in normalize_coach_list(raw)['coaches']}, {'unknown'})
        self.assertEqual(normalize_coach_list(service()['CoachList'][:1])['coaches'][0]['position'], 'frontAndRear')

    def test_fixed_icons_are_public_valid_svg_and_arbitrary_paths_are_rejected(self):
        app = ActionApplication(Mock(), api_key='test', base_url='https://rail.example')
        for name in ICONS:
            response = app.dispatch('GET', f'/icons/coach-{name}.svg', {}, {})
            self.assertEqual(response.status, 200)
            self.assertTrue(response.content_type.startswith('image/svg+xml'))
            root = ElementTree.fromstring(response.body)
            self.assertEqual(root.attrib['width'], '48')
            self.assertNotIn('<script', response.body)
            self.assertNotIn('href=', response.body)
        self.assertEqual(app.dispatch('GET', '/icons/coach-../../secret.svg', {}, {}).status, 404)

    def test_api_returns_icon_urls(self):
        client = Mock()
        client.get_service_details.return_value = normalize_coach_list(service()['CoachList'])
        app = ActionApplication(Mock(), api_key='test', base_url='https://rail.example', tiger_client=client)
        response = app.dispatch('GET', '/v1/tiger/service', {'station': ['PADTON'], 'uid': ['G01153']},
                                {'Authorization': 'Bearer test'})
        self.assertEqual(response.status, 200)
        self.assertTrue(response.body['result']['coaches'][-1]['iconUrl'].endswith('/coach-rear.svg'))
