#!/bin/sh
set -eu

. /etc/rtt-action.env
test -n "$ACTION_API_KEY"

curl -fsS https://rail.mikegtn.net/health
printf '\n'
curl -fsS \
    -H "Authorization: Bearer $ACTION_API_KEY" \
    https://rail.mikegtn.net/v1/info
printf '\n'
