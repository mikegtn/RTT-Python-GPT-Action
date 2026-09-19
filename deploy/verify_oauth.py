"""Host-only deployment test: real public OAuth and MCP, secrets kept in memory.

Exercises the owner bridge through its protected loopback interface. This is a
service integration test, not a substitute for browser login/consent testing.
"""
import argparse
import asyncio
import base64
import hashlib
from pathlib import Path
import secrets
from urllib.parse import parse_qs, urlsplit

import httpx
from verify_mcp import verify


async def main(args):
    base = 'https://rail.mikegtn.net'
    resource = base + '/mcp'
    callback = 'https://chatgpt.com/connector/oauth/deployment-verification'
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as client:
        response = await client.get(base + '/.well-known/oauth-protected-resource/mcp')
        assert response.json()['resource'] == resource
        response = await client.post(base + '/register', json={'client_name': 'RTT deployment verification',
            'redirect_uris': [callback], 'token_endpoint_auth_method': 'none', 'scope': 'rail:access',
            'grant_types': ['authorization_code', 'refresh_token'], 'response_types': ['code']})
        assert response.status_code == 201, 'Client registration failed'
        client_id = response.json()['client_id']
        response = await client.get(base + '/authorize', params={'client_id': client_id, 'response_type': 'code',
            'redirect_uri': callback, 'scope': 'rail:access', 'resource': resource, 'state': 'deployment-test',
            'code_challenge': challenge, 'code_challenge_method': 'S256'})
        assert response.status_code == 302, 'Authorization failed'
        flow = parse_qs(urlsplit(response.headers['location']).query)['request'][0]
        assert (await client.post(base + '/owner/approve', json={'request': flow, 'allow': True})).status_code == 404
        bridge_key = Path('/etc/rtt-oauth-bridge.key').read_text().strip()
        response = await client.post('http://127.0.0.1:8766/owner/approve', headers={'x-rtt-owner-key': bridge_key},
                                     json={'request': flow, 'allow': True})
        assert response.status_code == 200, 'Owner bridge failed'
        code = parse_qs(urlsplit(response.json()['redirect']).query)['code'][0]
        form = {'grant_type': 'authorization_code', 'client_id': client_id, 'code': code,
                'redirect_uri': callback, 'code_verifier': verifier, 'resource': resource}
        assert (await client.post(base + '/token', data={**form, 'code_verifier': 'wrong'})).status_code == 400
        response = await client.post(base + '/token', data=form)
        assert response.status_code == 200, 'Token exchange failed'
        token = response.json()
        try:
            assert (await client.post(base + '/token', data=form)).status_code == 400
            await verify(args, key=token['access_token'])
            refresh = {'grant_type': 'refresh_token', 'client_id': client_id,
                       'refresh_token': token['refresh_token'], 'resource': resource}
            response = await client.post(base + '/token', data=refresh)
            assert response.status_code == 200, 'Refresh failed'
            refreshed = response.json()
            assert (await client.post(resource, headers={'Authorization': 'Bearer ' + token['access_token']})).status_code == 401
            assert (await client.post(base + '/token', data=refresh)).status_code == 400
            assert (await client.post(resource, headers={'Authorization': 'Bearer ' + refreshed['access_token']})).status_code == 401
            print('OAuth passed: discovery, DCR, owner bridge, PKCE, MCP, single-use code, rotation and replay revocation. No tokens retained.')
        finally:
            await client.post(base + '/revoke', data={'client_id': client_id, 'token': token['refresh_token']})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protocol-only', action='store_true')
    parser.add_argument('--date', default='2026-09-19')
    parser.add_argument('--output')
    asyncio.run(main(parser.parse_args()))
