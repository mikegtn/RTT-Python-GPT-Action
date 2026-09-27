#!/bin/bash
# Forced SSH command for the dedicated GitHub Actions deployment key.
set -euo pipefail
exec 9>/run/lock/trainbrain-github-deploy.lock
flock -n 9 || { echo 'Another TrainBrain deployment or verifier is running.' >&2; exit 1; }

current=/opt/rtt-mcp/current
case "${SSH_ORIGINAL_COMMAND:-}" in
    trainbrain-deploy\ *)
        commit=${SSH_ORIGINAL_COMMAND#trainbrain-deploy }
        [[ $commit =~ ^[0-9a-f]{40}$ ]] || exit 2
        expected=$(git ls-remote https://github.com/mikegtn/RTT-Python-GPT-Action.git refs/heads/plugin-submission-draft | cut -f1)
        [[ $commit == "$expected" ]] || { echo 'Only the current publication branch commit may be deployed.' >&2; exit 2; }
        if [[ $(readlink -f "$current") == "/opt/rtt-mcp/releases/$commit" ]]; then
            echo 'Requested release is already active; verifying it.'
            "$current/.venv/bin/python" "$current/deploy/verify_mcp.py" --protocol-only
        else
            updater=$(mktemp /tmp/trainbrain-github-update.XXXXXX)
            trap 'rm -f -- "$updater"' EXIT
            curl -fsSL "https://raw.githubusercontent.com/mikegtn/RTT-Python-GPT-Action/$commit/deploy/update_mcp.sh" -o "$updater"
            bash "$updater" "$commit"
        fi
        ;;
    trainbrain-verify)
        "$current/.venv/bin/python" "$current/deploy/verify_mcp.py" --output /tmp/rtt-mcp-noauth-verification.json
        ;;
    trainbrain-verify-progress)
        "$current/.venv/bin/python" "$current/deploy/verify_mcp.py" --service-progress
        "$current/.venv/bin/python" "$current/deploy/verify_mcp.py" --service-progress --progress-image --output /tmp/rtt-mcp-progress-verification.json
        ;;
    *)
        echo 'This key is restricted to TrainBrain deployment and verification.' >&2
        exit 2
        ;;
esac
