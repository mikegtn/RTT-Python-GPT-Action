import copy
import io
import json
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from rtt_app.action_api import ActionApplication, build_openapi_schema, create_application
from rtt_app.tiger import TigerClient, TigerError, normalize_coach_list, reconcile_rtt_tiger, resolve_tiger_tiploc


# Synthetic fixture based on the agreed G01153 coach example; no credentials.
def service():
    return {'UID': 'G01153', 'DepartureDate': '2026-09-18', 'CoachList': [
        {'CoachNumber': 1, 'CoachLetter': 'F', 'LeadingPowerCar': True, 'Wheelchairs': True, 'StandardClass': True},
        {'CoachNumber': 2, 'CoachLetter': 'D', 'BikeStorage': True, 'StandardClass': True},
        {'CoachNumber': 3, 'CoachLetter': 'C', 'StandardClass': True},
        {'CoachNumber': 4, 'CoachLetter': 'B', 'StandardClass': True},
        {'CoachNumber': 5, 'CoachLetter': 'A', 'FirstClass': True, 'Wheelchairs': True},
    ]}


class CoachTests(unittest.TestCase):
    def test_known_example_sorted_and_raw_preserved(self):
        raw = service()['CoachList'][::-1]
        original = copy.deepcopy(raw)
        result = normalize_coach_list(raw)
        self.assertEqual(raw, original)
        self.assertEqual(result['rawCoachList'], original)
        self.assertTrue(result['orientationKnown'])
        self.assertEqual((result['frontCoach'], result['rearCoach']), ('F', 'A'))
        self.assertEqual(result['firstClassCoaches'], ['A'])
        self.assertEqual(result['wheelchairCoaches'], ['F', 'A'])
        self.assertEqual(result['bikeCoaches'], ['D'])
        self.assertEqual(result['totalCoaches'], 5)
        self.assertIsNone(result['coaches'][0]['catering'])

    def test_reverse_orientation_and_trailing_only(self):
        raw = service()['CoachList']
        raw[0]['LeadingPowerCar'] = False
        raw[-1]['LeadingPowerCar'] = True
        self.assertEqual(normalize_coach_list(raw)['frontCoach'], 'A')
        del raw[-1]['LeadingPowerCar']
        raw[0]['TrailingPowerCar'] = True
        self.assertEqual(normalize_coach_list(raw)['frontCoach'], 'A')

    def test_never_infer_from_letters_or_class(self):
        raw = service()['CoachList']
        del raw[0]['LeadingPowerCar']
        result = normalize_coach_list(raw)
        self.assertFalse(result['orientationKnown'])
        self.assertIsNone(result['frontCoach'])
        self.assertEqual(result['firstClassCoaches'], ['A'])

    def test_conflicting_internal_and_duplicate_markers(self):
        for index in [1, 4]:
            raw = service()['CoachList']
            raw[index]['LeadingPowerCar'] = True
            self.assertFalse(normalize_coach_list(raw)['orientationKnown'])
        raw = service()['CoachList']
        raw[0]['TrailingPowerCar'] = True
        self.assertFalse(normalize_coach_list(raw)['orientationKnown'])

    def test_missing_duplicate_and_invalid_numbers_disable_orientation(self):
        for number in [None, 1, True, -1]:
            raw = service()['CoachList']
            raw[1]['CoachNumber'] = number
            self.assertFalse(normalize_coach_list(raw)['orientationKnown'])

    def test_strings_and_unknown_flags(self):
        raw = service()['CoachList']
        raw[0].update(CoachNumber='1', LeadingPowerCar='true', FirstClass='false', Catering='unknown')
        result = normalize_coach_list(raw)
        self.assertTrue(result['orientationKnown'])
        self.assertFalse(result['coaches'][0]['firstClass'])
        self.assertIsNone(result['coaches'][0]['catering'])

    def test_missing_empty_and_malformed_coaches(self):
        for raw in [None, []]:
            result = normalize_coach_list(raw)
            self.assertFalse(result['orientationKnown'])
            self.assertIsNone(result['totalCoaches'])
        for raw in [{}, 'F,D,C', [None]]:
            with self.assertRaises(TigerError):
                normalize_coach_list(raw)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.client = TigerClient('synthetic-test-secret')
        self.response = Mock()
        self.response.__enter__ = Mock(return_value=self.response)
        self.response.__exit__ = Mock(return_value=False)
        self.client._opener = Mock()
        self.client._opener.open.return_value = self.response
        self.response.read.return_value = json.dumps([service()]).encode()

    def test_request_headers_url_and_timeout(self):
        result = self.client.get_service_details('pad', 'G01153', '2026-09-18')
        request = self.client._opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://tiger-api-portal.worldline.global/services/PAD')
        self.assertEqual(request.get_header('X-api-key'), 'synthetic-test-secret')
        self.assertEqual(request.get_header('Origin'), 'https://tiger.worldline.global')
        self.assertTrue(result['dateVerified'])
        self.assertNotIn('synthetic-test-secret', json.dumps(result))

    def test_exact_uid_only_and_duplicate_rejection(self):
        for payload, status in [([{'UID': 'G011530'}], 404), ([service(), service()], 409)]:
            self.response.read.return_value = json.dumps(payload).encode()
            with self.assertRaises(TigerError) as error:
                self.client.get_service_details('PAD', 'G01153')
            self.assertEqual(error.exception.status, status)

    def test_date_mismatch_and_absence(self):
        with self.assertRaises(TigerError) as error:
            self.client.get_service_details('PAD', 'G01153', '2026-09-17')
        self.assertEqual(error.exception.status, 404)
        item = service(); del item['DepartureDate']
        self.response.read.return_value = json.dumps([item]).encode()
        self.assertFalse(self.client.get_service_details('PAD', 'G01153', '2026-09-18')['dateVerified'])

    def test_service_wrappers(self):
        for wrapper in ['Services', 'services']:
            self.response.read.return_value = json.dumps({wrapper: [service()]}).encode()
            self.assertEqual(self.client.get_service_details('PAD', 'G01153')['totalCoaches'], 5)

    def test_invalid_payloads_and_credential_echo(self):
        for raw in [b'not json', b'null', b'{}', b'[null]', b'"synthetic-test-secret"', b'x' * 2_000_001]:
            self.response.read.return_value = raw
            with self.assertRaises(TigerError) as error:
                self.client.get_service_details('PAD', 'G01153')
            self.assertNotIn('synthetic-test-secret', str(error.exception))

    def test_safe_http_network_errors(self):
        for error in [HTTPError('https://example.test', 403, 'synthetic-test-secret', {}, io.BytesIO(b'synthetic-test-secret')),
                      URLError('synthetic-test-secret'), TimeoutError('synthetic-test-secret')]:
            self.client._opener.open.side_effect = error
            with self.assertRaises(TigerError) as caught:
                self.client.get_service_details('PAD', 'G01153')
            self.assertNotIn('synthetic-test-secret', str(caught.exception))

    def test_validation_before_network(self):
        for station, uid, day in [('PAD/../x', 'G01153', None), ('PAD', 'g01153', None), ('PAD', 'G01153', '2026-99-01')]:
            with self.assertRaises(ValueError):
                self.client.get_service_details(station, uid, day)
        self.client._opener.open.assert_not_called()

    def test_configuration_and_no_redirects(self):
        from rtt_app.tiger import _NoRedirect
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, '', {}, 'https://elsewhere.test'))
        for url in ['http://example.test', 'https://user:pass@example.test', 'https://example.test?key=x']:
            with self.assertRaises(ValueError):
                TigerClient('test', base_url=url)
        for timeout in [0, -1, float('nan'), 61]:
            with self.assertRaises(ValueError):
                TigerClient('test', timeout=timeout)


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.rtt = {'scheduleMetadata': {'uniqueIdentity': 'gb-nr:G01153:2026-09-18'},
                    'calls': [{'location': {'shortCodes': ['PAD']}, 'locationMetadata': {'allocationIndex': 0}}],
                    'allocationData': [{'allocationIndex': 0, 'passengerVehicles': 5}]}
        self.tiger = {**normalize_coach_list(service()['CoachList']), 'uid': 'G01153',
                      'departureDate': '2026-09-18', 'dateVerified': True, 'station': 'PAD'}

    def test_matching_evidence_retains_rtt(self):
        before = copy.deepcopy(self.rtt)
        result = reconcile_rtt_tiger(self.rtt, self.tiger, 'PAD')
        self.assertTrue(result['coachEnrichmentApplied'])
        self.assertEqual(result['rtt'], before)
        self.assertEqual(self.rtt, before)
        self.assertEqual(result['authority']['allocation'], 'RTT')

    def test_date_uid_station_and_unknown_date_block_enrichment(self):
        for field, value in [('departureDate', '2026-09-17'), ('uid', 'G99999'), ('station', 'BRI'), ('dateVerified', False)]:
            tiger = dict(self.tiger, **{field: value})
            self.assertFalse(reconcile_rtt_tiger(self.rtt, tiger, 'PAD')['coachEnrichmentApplied'])
        self.assertFalse(reconcile_rtt_tiger(self.rtt, self.tiger, 'BRI')['coachEnrichmentApplied'])

    def test_count_disagreement_keeps_both_sources(self):
        self.rtt['allocationData'][0]['passengerVehicles'] = 9
        result = reconcile_rtt_tiger(self.rtt, self.tiger, 'PAD')
        self.assertFalse(result['coachEnrichmentApplied'])
        self.assertEqual(result['conflicts'], [{'field': 'totalCoaches', 'rtt': 9, 'tiger': 5}])


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.get_service_details.return_value = {'uid': 'G01153'}
        self.app = ActionApplication(Mock(), api_key='action-test', base_url='https://rail.example', tiger_client=self.client)
        self.params = {'station': ['PAD'], 'tiploc': ['PADTON'], 'uid': ['G01153']}
        self.auth = {'Authorization': 'Bearer action-test'}

    def test_auth_dispatch_and_usage(self):
        self.assertEqual(self.app.dispatch('GET', '/v1/tiger/service', self.params, {}).status, 401)
        self.client.get_service_details.assert_not_called()
        response = self.app.dispatch('GET', '/v1/tiger/service', self.params, self.auth)
        self.assertEqual(response.status, 200)
        self.client.get_service_details.assert_called_once_with('PADTON', 'G01153', None)
        self.app.record_request('/v1/tiger/service', 200)
        self.assertEqual(self.app.get_usage()['requestsByEndpoint']['/v1/tiger/service'], 1)

    def test_missing_invalid_disabled_and_upstream(self):
        for params in [{}, {'station': ['PAD'], 'uid': ['bad']}]:
            self.assertEqual(self.app.dispatch('GET', '/v1/tiger/service', params, self.auth).status, 400)
        for status in [404, 409, 502]:
            self.client.get_service_details.side_effect = TigerError('safe error', status)
            self.assertEqual(self.app.dispatch('GET', '/v1/tiger/service', self.params, self.auth).status, status)
        self.app.tiger_client = None
        self.assertEqual(self.app.dispatch('GET', '/v1/tiger/service', self.params, self.auth).status, 503)
        self.assertEqual(self.app.dispatch('GET', '/health', {}, {}).status, 200)

    def test_schema_and_environment(self):
        schema = build_openapi_schema('https://rail.example')
        operation = schema['paths']['/v1/tiger/service']['get']
        self.assertEqual(operation['operationId'], 'getTigerServiceDetails')
        self.assertEqual([p['name'] for p in operation['parameters'] if p['required']], ['station', 'uid'])
        with patch('rtt_app.action_api.load_dotenv'), patch.dict('os.environ', {'RTT_TOKEN': 'test', 'ACTION_API_KEY': 'test'}, clear=True):
            self.assertIsNone(create_application().tiger_client)
            with patch.dict('os.environ', {'TIGER_API_KEY': 'synthetic-test-secret'}):
                app = create_application()
                self.assertIsInstance(app.tiger_client, TigerClient)
                self.assertNotIn('synthetic-test-secret', json.dumps(app.dispatch('GET', '/openapi.json', {}, {}).body))


class TiplocRegressionTests(unittest.TestCase):
    def setUp(self):
        self.rtt = {
            'scheduleMetadata': {'uniqueIdentity': 'gb-nr:G01153:2026-09-18'},
            'calls': [{'location': {'shortCodes': ['PAD'], 'longCodes': ['PADTON']}}],
        }

    def test_resolves_crs_from_rtt_and_accepts_direct_tiploc(self):
        self.assertEqual(resolve_tiger_tiploc('pad', self.rtt), 'PADTON')
        self.assertEqual(resolve_tiger_tiploc('PADTON'), 'PADTON')
        self.assertEqual(resolve_tiger_tiploc('PAD', tiploc='padton'), 'PADTON')

    def test_unresolved_or_ambiguous_crs_never_reaches_tiger(self):
        with self.assertRaises(ValueError):
            resolve_tiger_tiploc('PAD')
        self.rtt['calls'][0]['location']['longCodes'].append('OTHER')
        with self.assertRaises(ValueError):
            resolve_tiger_tiploc('PAD', self.rtt)
        self.assertEqual(resolve_tiger_tiploc('PAD', self.rtt, 'PADTON'), 'PADTON')

    def test_explicit_tiploc_must_match_known_rtt_station(self):
        with self.assertRaises(ValueError):
            resolve_tiger_tiploc('PAD', self.rtt, 'BRSTLTM')
        with self.assertRaises(ValueError):
            resolve_tiger_tiploc('PADTON', tiploc='BRSTLTM')

    def test_api_resolves_before_fetch_and_reconciles_using_tiploc(self):
        tools = Mock()
        tools.get_service_details.return_value = self.rtt
        client = TigerClient('synthetic-test-secret')
        client.services = Mock(return_value=[service()])
        app = ActionApplication(tools, api_key='test', base_url='https://rail.example', tiger_client=client)
        params = {'station': ['PAD'], 'uid': ['G01153'], 'departure_date': ['2026-09-18'],
                  'unique_identity': ['gb-nr:G01153:2026-09-18']}
        response = app.dispatch('GET', '/v1/tiger/service', params, {'Authorization': 'Bearer test'})
        self.assertEqual(response.status, 200)
        client.services.assert_called_once_with('PADTON')
        self.assertEqual(response.body['result']['tiger']['station'], 'PADTON')
        self.assertTrue(response.body['result']['coachEnrichmentApplied'])

    def test_api_crs_without_resolution_is_actionable_400(self):
        client = Mock()
        app = ActionApplication(Mock(), api_key='test', base_url='https://rail.example', tiger_client=client)
        response = app.dispatch('GET', '/v1/tiger/service', {'station': ['PAD'], 'uid': ['G01153']},
                                {'Authorization': 'Bearer test'})
        self.assertEqual(response.status, 400)
        self.assertIn('TIPLOC', response.body['error'])
        client.get_service_details.assert_not_called()

    def test_live_notfound_shape_is_404_and_unknown_application_errors_stay_502(self):
        client = TigerClient('synthetic-test-secret')
        client._opener = Mock()
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        client._opener.open.return_value = response
        for name, status in [('NotFound', 404), ('Unauthorized', 502)]:
            response.read.return_value = json.dumps({
                'name': name, 'detail': 'Location with TIPLOC PAD not found.'}).encode()
            with self.assertRaises(TigerError) as error:
                client.services('PADTON')
            self.assertEqual(error.exception.status, status)
            self.assertNotIn('Location with TIPLOC PAD', str(error.exception))

    def test_notfound_propagates_as_404_through_endpoint(self):
        client = TigerClient('synthetic-test-secret')
        client._opener = Mock()
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"name":"NotFound","detail":"Location with TIPLOC PAD not found."}'
        client._opener.open.return_value = response
        app = ActionApplication(Mock(), api_key='test', base_url='https://rail.example', tiger_client=client)
        result = app.dispatch('GET', '/v1/tiger/service', {'station': ['PADTON'], 'uid': ['G01153']},
                              {'Authorization': 'Bearer test'})
        self.assertEqual(result.status, 404)
        self.assertIn('location not found', result.body['error'])


class ReconciliationStationRegressionTests(unittest.TestCase):
    def test_explicit_tiploc_does_not_confirm_an_unrelated_requested_crs(self):
        rtt = {'scheduleMetadata': {'uniqueIdentity': 'gb-nr:G01153:2026-09-18'},
               'calls': [{'location': {'shortCodes': ['BRI'], 'longCodes': ['BRSTLTM']}}]}
        tiger = {**normalize_coach_list(service()['CoachList']), 'uid': 'G01153',
                 'departureDate': '2026-09-18', 'dateVerified': True, 'station': 'BRSTLTM'}
        result = reconcile_rtt_tiger(rtt, tiger, 'BRSTLTM', requested_station='PAD')
        self.assertFalse(result['coachEnrichmentApplied'])
