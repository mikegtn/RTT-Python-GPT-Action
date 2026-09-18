#!/bin/bash
# Update the existing RTT installation from one tested, pinned public commit.
set -euo pipefail
if [[ $EUID -ne 0 ]]; then
    echo 'Run this script as root on the existing RTT server.' >&2
    exit 1
fi
commit=${1:-}
if [[ ! $commit =~ ^[0-9a-f]{40}$ ]]; then
    echo 'Supply the full 40-character commit SHA.' >&2
    exit 1
fi
test -f /opt/rtt-action/rtt_app/action_api.py
stage=$(mktemp -d)
backup=''
changed=0
cleanup() {
    status=$?
    trap - EXIT
    if [[ $status -ne 0 && $changed -eq 1 ]]; then
        echo "Update failed; restoring $backup" >&2
        cp -a "$backup/." /opt/rtt-action/ || true
        systemctl restart rtt-action.service || true
    fi
    rm -rf -- "$stage"
    exit "$status"
}
trap cleanup EXIT
curl -fsSL "https://github.com/mikegtn/RTT-Python-GPT-Action/archive/$commit.tar.gz" \
    -o "$stage/release.tar.gz"
mkdir "$stage/source"
tar -xzf "$stage/release.tar.gz" --strip-components=1 -C "$stage/source"
(cd "$stage/source" && python3 -m unittest discover -v)
backup=$(mktemp -d /opt/rtt-action-backup.XXXXXX)
cp -a /opt/rtt-action/. "$backup/"
echo "Backup: $backup"
changed=1
install -d -m 0755 /opt/rtt-action/deploy
for file in action_api.py tiger.py tiger_schema.py; do
    install -m 0644 "$stage/source/rtt_app/$file" "/opt/rtt-action/rtt_app/$file"
done
install -m 0644 "$stage/source/deploy/verify_tiger.py" /opt/rtt-action/deploy/verify_tiger.py
systemctl restart rtt-action.service
ready=0
for attempt in 1 2 3 4 5; do
    if systemctl is-active --quiet rtt-action.service && \
       curl -fsS http://127.0.0.1:8765/health >/dev/null; then
        ready=1
        break
    fi
    sleep 1
done
test "$ready" -eq 1
echo "Installed $commit; service health check passed."
echo 'Now run: python3 /opt/rtt-action/deploy/verify_tiger.py --station PAD --tiger-station PADTON'
