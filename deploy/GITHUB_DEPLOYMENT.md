# GitHub deployment access

The `Deploy public MCP no-auth` workflow uses these repository Actions secrets:
`VPS_HOST`, `VPS_USER`, `VPS_PORT`, `VPS_SSH_KEY`, and `VPS_KNOWN_HOSTS`.
The known-hosts entry must come from an already verified connection to the VPS.

Use a dedicated Ed25519 key. Install its public key in the deployment user's
`authorized_keys` with this prefix:

```
restrict,command="/usr/local/sbin/trainbrain-github-deploy"
```

Install `deploy/github_ssh_dispatch.sh` at that path, owned by root with mode
0700, using the existing administrative connection. The current deployment user
is root because the pinned updater manages systemd and Apache. The forced
command denies interactive shells and arbitrary SSH commands; `restrict`
disables forwarding and PTYs. Repository deployment rights still permit code
from the publication branch to run through the privileged updater.

The dispatcher accepts only deployment of the current
`plugin-submission-draft` commit and the two fixed verification commands. It
serializes operations with a lock. A rerun for the active release verifies it
without reinstalling; an existing inactive release still requires inspection.
Workflow concurrency also prevents overlapping runs.

The workflow currently triggers when its own file changes on that branch.
It deploys the triggering commit's full SHA. Dispatcher changes require an
explicit administrative installation; they are not installed by the workflow.

When rotating the key, add the new restricted public key, replace the GitHub
secret, verify a successful run, then remove only the retired deployment key.
Never upload a personal administrative SSH private key as an Actions secret.
