<?php
declare(strict_types=1);
// Installed narrowly under the existing site's /admin/. No admin access is delegated.
require_once dirname(__DIR__) . '/includes/bootstrap.php';
require_once dirname(__DIR__) . '/includes/admin-auth.php';
// A cross-site OAuth arrival does not carry the Strict Admin cookie. Starting
// a session here would replace the existing login cookie with an empty session.
$crossSiteArrival = ($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'GET'
    && ($_SERVER['HTTP_SEC_FETCH_SITE'] ?? '') === 'cross-site';
if (!$crossSiteArrival) { admin_start_session(); }
header('Cache-Control: no-store');
header('Referrer-Policy: no-referrer');
header("Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'");
header('X-Frame-Options: DENY');
header('X-Content-Type-Options: nosniff');

function rtt_owner_call(string $operation, array $payload): array {
    $key = trim((string) @file_get_contents('/etc/rtt-oauth-bridge.key'));
    if (strlen($key) < 32) { throw new RuntimeException('Connection service unavailable.'); }
    $context = stream_context_create(['http' => [
        'method' => 'POST', 'timeout' => 10, 'follow_location' => 0,
        'header' => "Content-Type: application/json\r\nX-RTT-Owner-Key: " . $key . "\r\n",
        'content' => json_encode($payload, JSON_THROW_ON_ERROR),
    ]]);
    $body = @file_get_contents('http://127.0.0.1:8766/owner/' . $operation, false, $context, 0, 16384);
    if ($body === false) { throw new RuntimeException('This request has expired or the connection service is unavailable. Start again from ChatGPT.'); }
    $result = json_decode($body, true, 16, JSON_THROW_ON_ERROR);
    if (!is_array($result) || isset($result['error'])) { throw new RuntimeException('Invalid connection request.'); }
    return $result;
}

$flow = $_GET['request'] ?? '';
$error = '';
$pending = null;
$authenticated = !$crossSiteArrival && admin_session_is_authenticated();
if (!is_string($flow) || !preg_match('/\A[A-Za-z0-9_-]{43}\z/', $flow)) {
    $error = 'Start the connection from ChatGPT to create a valid request.';
    http_response_code(400);
} elseif ($authenticated) {
    if (!isset($_SESSION['rtt_oauth_csrf'])) { $_SESSION['rtt_oauth_csrf'] = bin2hex(random_bytes(32)); }
    try {
        if ($_SERVER['REQUEST_METHOD'] === 'POST') {
            $csrf = $_POST['csrf'] ?? '';
            $decision = $_POST['decision'] ?? '';
            if (!is_string($csrf) || !hash_equals($_SESSION['rtt_oauth_csrf'], $csrf)) {
                http_response_code(403);
                throw new RuntimeException('The security check failed. Reload this page and try again.');
            }
            if (!in_array($decision, ['allow', 'deny'], true)) {
                http_response_code(400);
                throw new RuntimeException('The consent choice was not submitted. Open the consent form again and choose Allow railway access or Cancel.');
            }
            $result = rtt_owner_call('approve', ['request' => $flow, 'allow' => $decision === 'allow']);
            $redirect = $result['redirect'] ?? '';
            if (!is_string($redirect) || !preg_match('~\Ahttps://chatgpt\.com/(?:connector_platform_oauth_redirect|connector/oauth/[A-Za-z0-9_-]{1,128})\?[^\r\n]+\z~', $redirect)) {
                throw new RuntimeException('Invalid return address.');
            }
            unset($_SESSION['rtt_oauth_csrf']);
            // The site's Apache CSP also enforces form-action 'self'. A 303
            // to ChatGPT remains part of the form submission and is blocked.
            // Finish the POST here, then navigate from the returned document.
            session_write_close();
            $escapedRedirect = htmlspecialchars($redirect, ENT_QUOTES, 'UTF-8');
            echo '<!doctype html><html lang="en"><head><meta charset="utf-8">'
                . '<meta name="referrer" content="no-referrer">'
                . '<meta http-equiv="refresh" content="0;url=' . $escapedRedirect . '">'
                . '<title>Returning to ChatGPT</title></head><body>'
                . '<h1>Returning to ChatGPT</h1><p>Your decision has been recorded.</p>'
                . '<p><a href="' . $escapedRedirect . '">Continue to ChatGPT</a></p></body></html>';
            exit;
        }
        $pending = rtt_owner_call('lookup', ['request' => $flow]);
    } catch (Throwable $exception) {
        $error = $exception instanceof RuntimeException ? $exception->getMessage() : 'Connection service unavailable.';
        if (http_response_code() < 400) { http_response_code(400); }
    }
}
?><!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Connect TrainBrain</title>
<style>body{font:18px/1.6 system-ui,sans-serif;background:#f1f6f5;color:#173c39;margin:0;padding:4vh 20px}main{max-width:620px;margin:auto;background:white;padding:32px;border-radius:16px}h1{line-height:1.2}button,.button{font:inherit;padding:12px 20px;background:#176b62;color:white;border:0;border-radius:7px;display:inline-block;cursor:pointer}button:focus,a:focus{outline:3px solid #eea936;outline-offset:3px}.secondary{background:#e4eeeb;color:#173c39}form{display:flex;gap:12px;flex-wrap:wrap}.error{color:#9b2727}small{display:block;margin-top:20px;color:#516661}</style>
<main><h1>Connect TrainBrain to ChatGPT</h1>
<?php if ($error !== ''): ?>
<p class="error"><?= htmlspecialchars($error, ENT_QUOTES, 'UTF-8') ?></p>
<p><a href="?request=<?= rawurlencode(is_string($flow) ? $flow : '') ?>">Open consent form again</a></p>
<?php elseif (!$authenticated): ?>
<p>Sign in to your existing website Admin account, then return here to continue.</p>
<p><a class="button" href="/admin/" target="_blank" rel="noopener noreferrer">Sign in to Admin</a></p>
<p><a href="?request=<?= rawurlencode($flow) ?>">I’m signed in — continue</a></p>
<?php else: ?>
<p>Allow ChatGPT to use TrainBrain for service searches, journey details, train reports, route maps and snapshots.</p>
<p>This grants access only to the railway tools. Your website stories, Admin controls and password are not shared.</p>
<p>Connection: <strong><?= htmlspecialchars((string) $pending['clientName'], ENT_QUOTES, 'UTF-8') ?></strong></p>
<form method="post" action="?request=<?= rawurlencode($flow) ?>">
<input type="hidden" name="csrf" value="<?= htmlspecialchars($_SESSION['rtt_oauth_csrf'], ENT_QUOTES, 'UTF-8') ?>">
<input type="hidden" name="decision" value="allow">
<button type="submit">Allow railway access</button>
</form>
<form method="post" action="?request=<?= rawurlencode($flow) ?>">
<input type="hidden" name="csrf" value="<?= htmlspecialchars($_SESSION['rtt_oauth_csrf'], ENT_QUOTES, 'UTF-8') ?>">
<input type="hidden" name="decision" value="deny">
<button type="submit" class="secondary">Cancel</button>
</form>
<small>Access tokens last one hour. ChatGPT can renew the connection for up to 30 days of inactivity. You can disconnect the app in ChatGPT.</small>
<?php endif; ?></main></html>
