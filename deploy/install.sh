#!/bin/sh
set -eu

test -d /opt/rtt-action/rtt_app
test -f /opt/rtt-action/rtt_app/action_api.py
test -f /etc/systemd/system/rtt-action.service
test -f /etc/apache2/sites-available/000-00-rail-action.conf
test -s /etc/lostmikegtn-rtt.key
test ! -e /etc/rtt-action.env

rtt_token="$(tr -d '\r\n' < /etc/lostmikegtn-rtt.key)"
test -n "$rtt_token"
action_key="$(openssl rand -hex 32)"
test "${#action_key}" -eq 64

env_tmp="$(mktemp /etc/rtt-action.env.XXXXXX)"
case "$env_tmp" in
    /etc/rtt-action.env.*) ;;
    *) echo "Unexpected temporary path" >&2; exit 1 ;;
esac
trap 'rm -f -- "$env_tmp"' EXIT HUP INT TERM
umask 077
{
    printf 'RTT_TOKEN=%s\n' "$rtt_token"
    printf 'RTT_TOKEN_TYPE=auto\n'
    printf 'RTT_API_VERSION=2026-07-25\n'
    printf 'ACTION_API_KEY=%s\n' "$action_key"
    printf 'ACTION_BASE_URL=https://rail.mikegtn.net\n'
} > "$env_tmp"
chown root:www-data "$env_tmp"
chmod 0640 "$env_tmp"
mv "$env_tmp" /etc/rtt-action.env
trap - EXIT HUP INT TERM

chown -R root:root /opt/rtt-action
find /opt/rtt-action -type d -exec chmod 0755 {} \;
find /opt/rtt-action -type f -exec chmod 0644 {} \;
chmod 0644 /etc/systemd/system/rtt-action.service
chmod 0644 /etc/apache2/sites-available/000-00-rail-action.conf

/usr/bin/python3 -m compileall -q /opt/rtt-action/rtt_app
systemctl daemon-reload
systemctl enable --now rtt-action.service

ready=false
for delay in 1 1 2 3 5; do
    if curl -fsS http://127.0.0.1:8765/health >/dev/null; then
        ready=true
        break
    fi
    sleep "$delay"
done
if [ "$ready" != true ]; then
    systemctl status rtt-action.service --no-pager >&2 || true
    exit 1
fi

a2enmod proxy proxy_http >/dev/null
a2ensite 000-00-rail-action.conf >/dev/null
if ! apache2ctl configtest; then
    a2dissite 000-00-rail-action.conf >/dev/null || true
    exit 1
fi
systemctl reload apache2

printf 'RTT_ACTION_INSTALLED\n'
systemctl is-active rtt-action.service
systemctl is-active apache2
