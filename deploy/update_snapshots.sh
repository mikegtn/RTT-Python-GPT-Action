#!/bin/bash
# Narrow, backed-up update of the existing service; never runs install.sh.
set -euo pipefail
test "$EUID" -eq 0
commit=${1:-}
[[ $commit =~ ^[0-9a-f]{40}$ ]]
test -f /opt/rtt-action/rtt_app/action_api.py
python3 -c 'from PIL import Image, ImageDraw, ImageFont'
stage=$(mktemp -d)
backup=''
changed=0
cleanup() {
    status=$?
    trap - EXIT
    if [[ $status -ne 0 && $changed -eq 1 ]]; then
        echo "Update failed; restoring $backup/application" >&2
        cp -a "$backup/application/." /opt/rtt-action/
        systemctl restart rtt-action.service
    fi
    rm -rf -- "$stage"
    exit "$status"
}
trap cleanup EXIT
curl -fsSL "https://github.com/mikegtn/RTT-Python-GPT-Action/archive/$commit.tar.gz" -o "$stage/release.tar.gz"
mkdir "$stage/source"
tar -xzf "$stage/release.tar.gz" --strip-components=1 -C "$stage/source"
(cd "$stage/source" && python3 -m unittest discover -v)
backup=$(mktemp -d /opt/rtt-action-backup.XXXXXX)
mkdir "$backup/application"
cp -a /opt/rtt-action/. "$backup/application/"
cp -a /etc/rtt-action.env /etc/systemd/system/rtt-action.service "$backup/"
echo "Backup: $backup"
changed=1
for file in action_api.py map_snapshot.py; do
    install -m 0644 "$stage/source/rtt_app/$file" "/opt/rtt-action/rtt_app/$file"
done
install -m 0644 "$stage/source/GPT_ACTION_INSTRUCTIONS.md" /opt/rtt-action/GPT_ACTION_INSTRUCTIONS.md
cmp -s /etc/rtt-action.env "$backup/rtt-action.env"
cmp -s /etc/systemd/system/rtt-action.service "$backup/rtt-action.service"
systemctl restart rtt-action.service
ready=0
for attempt in 1 2 3 4 5; do
    if systemctl is-active --quiet rtt-action.service && curl -fsS http://127.0.0.1:8765/health >/dev/null; then
        ready=1
        break
    fi
    sleep 1
done
test "$ready" -eq 1
for file in action_api.py map_snapshot.py; do
    cmp -s "$stage/source/rtt_app/$file" "/opt/rtt-action/rtt_app/$file"
done
echo "Installed $commit; health, source files and unchanged secrets verified."
