import asyncio
import base64
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import time
import unittest
from urllib.parse import parse_qs, urlsplit


@unittest.skipUnless(importlib.util.find_spec('mcp'), 'Install mcp extra')
class OAuthTests(unittest.TestCase):
    def setUp(self):
        from starlette.testclient import TestClient
        from rtt_app.mcp_oauth import OwnerOAuth, RESOURCE
        from rtt_app.mcp_server import create_http_app, create_server
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.oauth = OwnerOAuth(Path(self.tmp.name) / 'oauth.db', 'bridge-key-' * 5)
        self.resource = RESOURCE
        self.backend_data = {}
        async def backend(path, params):
            return {'ok': True, 'result': self.backend_data, 'requestEvidence': {'requestId': 'oauth-source'}}
        self.client = TestClient(create_http_app(create_server(backend, oauth_enabled=True), 'legacy-key', self.oauth),
                                 client=('127.0.0.1', 1234))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.callback = 'https://chatgpt.com/connector/oauth/test-123'
        response = self.client.post('/register', json={'client_name': 'ChatGPT',
            'redirect_uris': [self.callback], 'token_endpoint_auth_method': 'none',
            'grant_types': ['authorization_code', 'refresh_token'], 'response_types': ['code'], 'scope': 'rail:access'})
        self.assertEqual(response.status_code, 201, response.text)
        self.client_id = response.json()['client_id']
        self.verifier = 'v' * 64
        self.challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).decode().rstrip('=')

    def authorize(self, **changes):
        params = {'client_id': self.client_id, 'redirect_uri': self.callback, 'response_type': 'code',
                  'scope': 'rail:access', 'resource': self.resource, 'state': 'unchanged-state',
                  'code_challenge': self.challenge, 'code_challenge_method': 'S256'}
        params.update(changes)
        return self.client.get('/authorize', params=params, follow_redirects=False)

    def approve(self, allow=True):
        response = self.authorize()
        self.assertEqual(response.status_code, 302, response.text)
        flow = parse_qs(urlsplit(response.headers['location']).query)['request'][0]
        headers = {'x-rtt-owner-key': self.oauth.bridge_key}
        self.assertEqual(self.client.post('/owner/approve', json={'request': flow, 'allow': True}).status_code, 401)
        response = self.client.post('/owner/approve', headers=headers, json={'request': flow, 'allow': allow})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.post('/owner/approve', headers=headers, json={'request': flow, 'allow': allow}).status_code, 400)
        return parse_qs(urlsplit(response.json()['redirect']).query)

    def token(self, code, **changes):
        params = {'grant_type': 'authorization_code', 'client_id': self.client_id, 'code': code,
                  'redirect_uri': self.callback, 'code_verifier': self.verifier, 'resource': self.resource}
        params.update(changes)
        return self.client.post('/token', data=params)

    def rpc(self, token, method='tools/list', params=None):
        return self.client.post('/mcp', headers={'Authorization': 'Bearer ' + token,
            'Accept': 'application/json, text/event-stream'}, json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}})

    def test_owner_pkce_resource_refresh_replay_and_persistence(self):
        reply = self.approve()
        self.assertEqual(reply['state'], ['unchanged-state'])
        code = reply['code'][0]
        self.assertEqual(self.token(code, code_verifier='bad').status_code, 400)
        self.assertEqual(self.token(code, resource='https://other.example/mcp').status_code, 400)
        self.assertEqual(self.token(code, redirect_uri='https://chatgpt.com/connector/oauth/other').status_code, 400)
        response = self.token(code)
        self.assertEqual(response.status_code, 200, response.text)
        token = response.json()
        self.assertEqual(self.token(code).status_code, 400)
        catalog = self.rpc(token['access_token']).json()['result']['tools']
        self.assertEqual(len(catalog), 13)
        for tool in catalog:
            self.assertEqual(tool['securitySchemes'], [{'type': 'oauth2', 'scopes': ['rail:access']}])
            self.assertEqual(tool['securitySchemes'], tool['_meta']['securitySchemes'])
        self.assertEqual(catalog[0]['_meta']['securitySchemes'][0]['scopes'], ['rail:access'])
        from tests.test_service_progress import fixture
        self.backend_data = fixture()
        progress = self.rpc(token['access_token'], 'tools/call', {'name': 'getServiceProgress',
            'arguments': {'unique_identity': 'opaque', 'as_of': '2026-09-19T20:35:00+01:00'}}).json()['result']
        self.assertFalse(progress.get('isError'))
        self.assertEqual(progress['structuredContent']['result']['state'], 'between_calls')
        self.assertEqual(progress['structuredContent']['sourceRequestEvidence'], [{'requestId': 'oauth-source'}])
        from rtt_app.mcp_oauth import OwnerOAuth
        reopened = OwnerOAuth(self.oauth.database, self.oauth.bridge_key)
        self.assertIsNotNone(asyncio.run(reopened.load_access_token(token['access_token'])))
        raw = Path(self.oauth.database).read_bytes()
        self.assertNotIn(token['access_token'].encode(), raw)
        self.assertNotIn(token['refresh_token'].encode(), raw)
        refresh = {'grant_type': 'refresh_token', 'client_id': self.client_id,
                   'refresh_token': token['refresh_token'], 'resource': self.resource}
        new = self.client.post('/token', data=refresh)
        self.assertEqual(new.status_code, 200, new.text)
        self.assertEqual(self.rpc(token['access_token']).status_code, 401)
        self.assertEqual(self.rpc(new.json()['access_token']).status_code, 200)
        self.assertEqual(self.client.post('/token', data=refresh).status_code, 400)
        self.assertEqual(self.rpc(new.json()['access_token']).status_code, 401)

    def test_discovery_rejection_denial_and_revocation(self):
        response = self.client.post('/mcp')
        self.assertEqual(response.status_code, 401)
        self.assertIn('resource_metadata=', response.headers['www-authenticate'])
        self.assertEqual(self.client.get('/.well-known/oauth-protected-resource/mcp').json()['resource'], self.resource)
        self.assertEqual(self.client.get('/.well-known/oauth-authorization-server').json()['code_challenge_methods_supported'], ['S256'])
        self.assertEqual(self.authorize(code_challenge_method='plain').status_code, 302)
        self.assertIn('error=', self.authorize(resource='wrong').headers['location'])
        response = self.client.post('/register', json={'redirect_uris': ['https://attacker.example/callback']})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.approve(False)['error'], ['access_denied'])
        token = self.token(self.approve()['code'][0]).json()
        response = self.client.post('/revoke', data={'client_id': self.client_id, 'token': token['refresh_token']})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.rpc(token['access_token']).status_code, 401)
        self.assertEqual(self.rpc('legacy-key').status_code, 200)

    def test_expiry_wrong_client_and_body_limits(self):
        from rtt_app.mcp_oauth import digest
        code = self.approve()['code'][0]
        with self.oauth.transaction() as db:
            db.execute("UPDATE records SET expires=? WHERE kind='code'", (time.time() - 1,))
        self.assertEqual(self.token(code).status_code, 400)
        token = self.token(self.approve()['code'][0]).json()
        with self.oauth.transaction() as db:
            db.execute("UPDATE records SET expires=? WHERE kind='access' AND key=?", (time.time() - 1, digest(token['access_token'])))
        self.assertEqual(self.rpc(token['access_token']).status_code, 401)
        self.assertEqual(self.client.post('/register', content=b'x' * 17000).status_code, 413)

    def test_movebook_resource_exact_bytes(self):
        uri = 'skill://realtime-trains/realtime-trains/references/MOVEBOOK.md'
        response = self.rpc('legacy-key', 'resources/read', {'uri': uri})
        text = response.json()['result']['contents'][0]['text']
        source = Path(__file__).resolve().parents[1] / 'plugins/realtime-trains/skills/realtime-trains/references/MOVEBOOK.md'
        self.assertEqual(text, source.read_text(encoding='utf-8'))
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), '429e116b6fb27220901e20f480b14b158c59c7cca7e89db578a40e81a26d5e17')

    def test_confidential_client_authentication_and_binding(self):
        public_id = self.client_id
        response = self.client.post('/register', json={'redirect_uris': [self.callback],
            'token_endpoint_auth_method': 'client_secret_post', 'scope': 'rail:access',
            'grant_types': ['authorization_code', 'refresh_token'], 'response_types': ['code']})
        self.assertEqual(response.status_code, 201, response.text)
        confidential = response.json()
        self.client_id = confidential['client_id']
        code = self.approve()['code'][0]
        self.assertEqual(self.token(code, client_id=public_id).status_code, 400)
        self.assertEqual(self.token(code).status_code, 401)
        self.assertEqual(self.token(code, client_secret='wrong').status_code, 401)
        response = self.token(code, client_secret=confidential['client_secret'])
        self.assertEqual(response.status_code, 200, response.text)
        token = response.json()
        self.assertEqual(self.client.post('/token', data={'client_id': public_id,
            'grant_type': 'refresh_token', 'resource': self.resource, 'refresh_token': token['refresh_token']}).status_code, 400)
        self.assertEqual(self.rpc(token['access_token']).status_code, 200)
        self.assertEqual(self.client.post('/revoke', data={'client_id': self.client_id,
            'client_secret': confidential['client_secret'], 'token': token['access_token']}).status_code, 200)
        self.assertEqual(self.rpc(token['access_token']).status_code, 401)
