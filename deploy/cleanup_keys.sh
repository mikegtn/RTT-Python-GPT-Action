#!/bin/sh
set -eu

auth=/root/.ssh/authorized_keys
stale_tmp=/root/.ssh/authorized_keys.RqcZrW
test -f "$auth"
test -f "$stale_tmp"
test ! -s "$stale_tmp"
test "$(grep -c ' rtt-action-deploy$' "$auth")" -eq 1
test "$(grep -c ' rtt-action-deploy-v2$' "$auth")" -eq 1

rm -- "$stale_tmp"

new_auth="$(mktemp /root/.ssh/authorized_keys.XXXXXX)"
case "$new_auth" in
    /root/.ssh/authorized_keys.*) ;;
    *) echo "Unexpected temporary path" >&2; exit 1 ;;
esac
trap 'rm -f -- "$new_auth"' EXIT HUP INT TERM
grep -v ' rtt-action-deploy$' "$auth" > "$new_auth"
test "$(grep -c ' rtt-action-deploy$' "$new_auth" || true)" -eq 0
test "$(grep -c ' rtt-action-deploy-v2$' "$new_auth")" -eq 1
chown root:root "$new_auth"
chmod 0600 "$new_auth"
mv "$new_auth" "$auth"
trap - EXIT HUP INT TERM

printf 'OLD_KEY_REMOVED\n'
