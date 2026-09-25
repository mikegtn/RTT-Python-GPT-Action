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

## Connection

The packaged `.mcp.json` uses OAuth discovery without a static Authorization
header. ChatGPT registers its client dynamically; leave optional client ID and
client secret fields empty when using dynamic registration. Each tool advertises
`rail:access` in both `securitySchemes` and the compatibility `_meta` field.
Compatible private MCP clients can still provide an Authorization bearer header.
Configure secrets in the client, never in this package or in chat. The default server
configuration accepts the existing Action key; `MCP_API_KEY` can separate the
inbound MCP credential from the upstream Action credential.

ChatGPT connects using OAuth at the same MCP URL. Discovery, dynamic client
registration, S256 PKCE and exact ChatGPT callback matching are supported.
Start account linking in ChatGPT, sign in to the existing mikegtn.net Admin
account if necessary, then approve railway access. The scope is `rail:access`;
this grants no website administration rights. The SDK validates clients and
PKCE; the provider validates the resource on authorization and token exchange.
Access tokens last one hour. Refresh tokens rotate and expire after 30 days of
inactivity; replay revokes the token family. Codes and bearer tokens are hashed
in the durable private database. `/revoke` revokes the entire token family.
The owner bridge uses a separate host-generated key and loopback endpoints.
Account installation must be verified separately from server tests. After
registration, use the actual returned app ID; no fabricated ID is included here.

The live GPT editor was inspected on 19 September 2026. Its instructions match
`references/gpt-instructions.md`; its Action uses the nine operations captured
in `migration/action-openapi.json`. That schema is generated from the matching
repository builder, not an exported browser file. The editor lists one knowledge
attachment, `MOVEBOOK.md`, recovered from the owner's Downloads folder and
preserved byte for byte in the skill's references. Its SHA-256 is
`429e116b6fb27220901e20f480b14b158c59c7cca7e89db578a40e81a26d5e17`.
The skill and both reference files are available through authenticated MCP
resource reads. No migration option was visible in
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
the MCP and explicit OAuth routes ahead of the existing Action proxy, plus a
small consent page under the existing website Admin path. It preserves the Action service,
environment, maps and usage counters. It rolls back configuration and the
consent page on failure. OAuth state and the bridge key survive updates.
`OAUTH_DATABASE` and `OAUTH_BRIDGE_KEY_FILE` enable OAuth in the supplied service
unit. Back up the private OAuth database securely; never include it in packages.
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
