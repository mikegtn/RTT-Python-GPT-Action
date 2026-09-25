"""Exercise the real PHP consent page with isolated login and owner-bridge stubs."""
import http.client
import re
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import urlencode


@unittest.skipUnless(shutil.which('php'), 'PHP required for consent page integration')
class ConsentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / 'admin').mkdir()
        (root / 'includes').mkdir()
        (root / 'includes/bootstrap.php').write_text('<?php')
        (root / 'includes/admin-auth.php').write_text('''<?php
function admin_start_session(): void {
 session_name('LOSTADMIN');
 session_set_cookie_params(['path'=>'/admin','httponly'=>true,'samesite'=>'Strict']);
 session_start();
}
function admin_session_is_authenticated(): bool { return !empty($_SESSION['admin_authenticated']); }
''')
        (root / 'admin/login.php').write_text('''<?php
require '../includes/admin-auth.php'; admin_start_session();
$_SESSION['admin_authenticated'] = true; echo 'test login';
''')
        source = (Path(__file__).resolve().parents[1] / 'deploy/rtt-oauth.php').read_text()
        start = source.index('function rtt_owner_call(')
        end = source.index("$flow = $_GET", start)
        source = source[:start] + '''function rtt_owner_call(string $operation, array $payload): array {
 if ($operation === 'lookup') return ['clientName'=>'Isolated test client'];
 return ['redirect'=>'https://chatgpt.com/connector/oauth/test?' .
     ($payload['allow'] ? 'code=test-only' : 'error=access_denied')];
}

''' + source[end:]
        (root / 'admin/rtt-oauth.php').write_text(source)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            self.port = sock.getsockname()[1]
        self.server = subprocess.Popen(['php', '-S', f'127.0.0.1:{self.port}', '-t', str(root)],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(self.stop)
        self.cookie = ''
        for _ in range(50):
            try:
                self.request('/admin/login.php')
                break
            except ConnectionRefusedError:
                time.sleep(.02)
        else:
            self.fail('PHP server did not start')
        self.path = '/admin/rtt-oauth.php?request=' + 'x' * 43

    def stop(self):
        self.server.terminate()
        self.server.wait(timeout=5)

    def request(self, path, data=None, headers=None, cookie=True):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        hdr = dict(headers or {})
        if cookie and self.cookie:
            hdr['Cookie'] = self.cookie
        if data is not None:
            hdr['Content-Type'] = 'application/x-www-form-urlencoded'
        conn.request('POST' if data is not None else 'GET', path,
                     urlencode(data) if data is not None else None, hdr)
        response = conn.getresponse()
        status, fields, body = response.status, dict(response.getheaders()), response.read().decode()
        if fields.get('Set-Cookie'):
            self.cookie = fields['Set-Cookie'].split(';')[0]
        conn.close()
        return status, fields, body

    def form(self, decision):
        status, _, body = self.request(self.path)
        self.assertEqual(status, 200)
        forms = re.findall(r'<form.*?</form>', body, re.S)
        for form in forms:
            fields = dict(re.findall(r'name="([^"]+)" value="([^"]*)"', form))
            if fields.get('decision') == decision:
                return fields
        self.fail('Consent decision must be in a hidden form field')

    def test_allow_and_deny_with_serialized_form_fields(self):
        for decision, result in [('allow', 'code=test-only'), ('deny', 'error=access_denied')]:
            fields = self.form(decision)
            status, headers, body = self.request(self.path, fields)
            self.assertEqual(status, 200)
            self.assertNotIn('Location', headers)
            self.assertIn('http-equiv="refresh"', body)
            self.assertIn('https://chatgpt.com/connector/oauth/test?' + result, body)
            self.assertIn('Continue to ChatGPT', body)
            self.assertIn("form-action 'self';", headers['Content-Security-Policy'])

    def test_csrf_and_missing_decision_fail_closed(self):
        fields = self.form('allow')
        status, headers, body = self.request(self.path, {**fields, 'csrf': 'wrong'})
        self.assertEqual(status, 403)
        self.assertNotIn('Location', headers)
        self.assertIn('Open consent form again', body)
        fields.pop('decision')
        status, headers, body = self.request(self.path, fields)
        self.assertEqual(status, 400)
        self.assertNotIn('Location', headers)
        self.assertIn('consent choice was not submitted', body)

    def test_cross_site_arrival_preserves_existing_login(self):
        original = self.cookie
        status, headers, body = self.request(self.path, headers={'Sec-Fetch-Site':'cross-site'}, cookie=False)
        self.assertEqual(status, 200)
        self.assertNotIn('Set-Cookie', headers)
        self.assertIn('I’m signed in', body)
        self.assertEqual(self.cookie, original)
        self.assertIn('csrf', self.form('allow'))

    def test_unauthenticated_post_cannot_approve(self):
        status, headers, body = self.request(self.path, {'csrf':'wrong','decision':'allow'}, cookie=False)
        self.assertEqual(status, 200)
        self.assertNotIn('Location', headers)
        self.assertIn('Sign in to Admin', body)
