# Realtime Trains plugin migration

This package preserves the Realtime Trains Assistant behaviour and exposes twelve
MCP tools through `https://rail.mikegtn.net/mcp`. It is a private, authenticated
integration. The original GPT Action continues to run independently.

## Tools

The nine existing operations retain their names: `getNextDepartures`,
`searchStationServices`, `getServiceDetails`, `getTigerServiceDetails`,
`getRttApiInfo`, `getApiUsage`, `suggestRailRoute`, `getJourneyRoute`, and
`getRailMapSnapshot`. MCP `getJourneyRoute.legs` is a native array; the adapter
encodes it for the existing Action internally. Results preserve `uniqueIdentity`
and `requestEvidence` without rewriting them.

Additional tools:

- `findJourneys`: direct trains and up to three caller-supplied interchanges;
  six candidates per search, up to three returned options. Uses advertised
  times, passenger call restrictions, cancellations and chronological checks.
  It is not an exhaustive national journey planner. A connection buffer is an
  assumption, not a sourced minimum interchange time.
- `getTrainLocation`: last actual RTT timing report, its timestamp and age.
  Never GPS and never a forecast presented as an observed position.
- `getRouteDetails`: ordered service-detail calls/timing points, without
  claiming geometry, distance, or a complete technical schedule.

Composite tools return their own audit event plus `sourceRequestEvidence` for
the backend calls actually completed. Maps and snapshots are annotated as
non-destructive writes because they create persistent artifacts.

## Connection and remaining gates

The remote endpoint uses bearer authentication. Compatible MCP clients can
provide an Authorization header. `.mcp.json` references the caller's
`RTT_MCP_API_KEY` environment variable; it contains no credential. Configure
secrets in the client, never in this package or in chat. The default server
configuration accepts the existing Action key; `MCP_API_KEY` can separate the
inbound MCP credential from the upstream Action credential.

ChatGPT's documented authenticated MCP connection uses OAuth 2.1. No OAuth
issuer, client registration, or account-linking credentials were supplied or
found in this repository. This bearer endpoint must not be advertised as a
completed ChatGPT installation. Configure a real OAuth provider and validate
issuer, audience, scopes and token lifetime before account linking. Do not use
anonymous access as a workaround. After registration, use the actual returned
`plugin_asdk_app...` ID in `.app.json`; no fabricated app ID is included here.

The live GPT editor was inspected on 19 September 2026. Its instructions match
`references/gpt-instructions.md`; its Action uses the nine operations captured
in `migration/action-openapi.json`. That schema is generated from the matching
repository builder, not an exported browser file. The editor lists one knowledge
attachment, `MOVEBOOK.md`, but clicking it did not expose or download its content.
That knowledge file has not been migrated. No migration option was visible in
the GPT editor's menu. Web Search, Image Generation and Code Interpreter were
disabled. The GPT was private (Only me).

## Run and deploy

From the repository, install the `mcp` extra and run
`python -m rtt_app.mcp_server --transport http` (loopback port 8766) or use
`--transport stdio`. `ACTION_API_KEY` and optionally `MCP_BACKEND_URL` configure
the backend; production uses loopback HTTP, remote backends require HTTPS.
No OpenAI API key is needed: this server provides tools, not model inference.

`deploy/update_mcp.sh COMMIT_SHA` installs a pinned release in a separate virtual
environment, runs tests, backs up the Apache/service configuration, and adds
only `/mcp` ahead of the existing Action proxy. It preserves the Action service,
environment, maps and usage counters. It rolls back configuration on failure.
Never run `deploy/install.sh` to add this adapter.

Run `python -m unittest discover -v` with the MCP extra installed. On the host,
`deploy/verify_mcp.py --output /tmp/rtt-mcp-verification.json` checks real HTTPS
MCP initialization, tools, resources, authentication and Aberdeen to Plymouth,
including service details, location, route details, map and PNG snapshot. The
output contains audit evidence but no credentials.

## Official implementation references

- [Build an MCP server](https://developers.openai.com/plugins/build/mcp-server)
- [Authentication](https://developers.openai.com/plugins/build/auth)
- [Package a plugin](https://developers.openai.com/plugins/build/plugins)
- [Connect and test](https://developers.openai.com/plugins/deploy/connect-chatgpt)
