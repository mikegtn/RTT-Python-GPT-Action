#!/bin/bash
# Add/update the isolated public MCP sidecar; never rerun the Action installer.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run as root on the existing RTT host.' >&2; exit 1; }
commit=${1:-}
[[ $commit =~ ^[0-9a-f]{40}$ ]] || { echo 'Supply a full commit SHA.' >&2; exit 1; }
test -f /etc/rtt-action.env
config=$(readlink -f /etc/apache2/sites-enabled/000-00-rail-action.conf)
test -f "$config"

root=/opt/rtt-mcp
release="$root/releases/$commit"
mkdir -p "$root/releases"
test ! -e "$release" || { echo 'Release already exists; inspect before retrying.' >&2; exit 1; }
mkdir "$release"

backup=$(mktemp -d /opt/rtt-mcp-backup.XXXXXX)
cp -a "$config" "$backup/apache.conf"
bridge=/var/www/html/admin/rtt-oauth.php
if test -f "$bridge"; then cp -a "$bridge" "$backup/rtt-oauth.php"; fi
previous=$(readlink "$root/current" || true)
if test -f /etc/systemd/system/rtt-mcp.service; then
    cp -a /etc/systemd/system/rtt-mcp.service "$backup/rtt-mcp.service"
fi

changed=0
cleanup() {
    status=$?
    trap - EXIT
    if [[ $status -ne 0 && $changed -eq 1 ]]; then
        echo "MCP update failed; restoring $backup" >&2
        cp -a "$backup/apache.conf" "$config"
        if test -f "$backup/rtt-oauth.php"; then
            cp -a "$backup/rtt-oauth.php" "$bridge"
        else
            rm -f -- "$bridge"
        fi
        if test -n "$previous"; then
            ln -sfn "$previous" "$root/current"
        fi
        if test -f "$backup/rtt-mcp.service"; then
            cp -a "$backup/rtt-mcp.service" /etc/systemd/system/rtt-mcp.service
            systemctl daemon-reload
            systemctl restart rtt-mcp.service || true
        else
            systemctl disable --now rtt-mcp.service || true
        fi
        apache2ctl configtest && systemctl reload apache2 || true
    fi
    exit "$status"
}
trap cleanup EXIT

curl -fsSL "https://github.com/mikegtn/RTT-Python-GPT-Action/archive/$commit.tar.gz" -o "$release/source.tar.gz"
tar -xzf "$release/source.tar.gz" --strip-components=1 -C "$release"
python3 -m venv "$release/.venv"
"$release/.venv/bin/pip" install --quiet "$release[mcp,snapshots]"
(cd "$release" && .venv/bin/python -m unittest discover -q)

changed=1
ln -sfn "$release" "$root/current"
install -m 0644 "$release/deploy/rtt-mcp.service" /etc/systemd/system/rtt-mcp.service

# The public plugin no longer uses owner OAuth. Remove only the legacy public
# OAuth proxy rules and consent page; keep the old private database/key on disk
# for rollback until the no-auth release has been proven in production.
rm -f -- "$bridge"
python3 - "$config" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
marker = '    ProxyPass / http://127.0.0.1:8765/'
if 'ProxyPass /mcp ' not in text:
    if text.count(marker) != 1:
        raise SystemExit('Expected Action proxy rule missing or ambiguous')
    text = text.replace(
        marker,
        '    ProxyPass /mcp http://127.0.0.1:8766/mcp connectiontimeout=5 timeout=200\n'
        '    ProxyPassReverse /mcp http://127.0.0.1:8766/mcp\n' + marker,
    )

oauth_endpoints = {
    '/.well-known/oauth-authorization-server',
    '/.well-known/oauth-protected-resource',
    '/authorize',
    '/token',
    '/register',
    '/revoke',
}
kept = []
for line in text.splitlines():
    stripped = line.strip()
    legacy = False
    for endpoint in oauth_endpoints:
        if stripped.startswith(f'ProxyPass {endpoint} ') or stripped.startswith(f'ProxyPassReverse {endpoint} '):
            legacy = True
            break
    if not legacy:
        kept.append(line)
path.write_text('\n'.join(kept) + '\n')
PY

apache2ctl configtest
systemctl daemon-reload
systemctl enable --now rtt-mcp.service
systemctl restart rtt-mcp.service

ready=0
for attempt in 1 2 3 4 5; do
    if curl -fsS http://127.0.0.1:8766/health >/dev/null; then ready=1; break; fi
    sleep 1
done
test "$ready" -eq 1

systemctl reload apache2
"$release/.venv/bin/python" "$release/deploy/verify_mcp.py" --protocol-only
curl -fsS https://rail.mikegtn.net/health

echo "Installed public no-auth MCP $commit. Backup: $backup. Existing Action and upstream secrets preserved."
