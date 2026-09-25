# Realtime Trains plugin

This package exposes the Realtime Trains rail assistant through the public,
read-only MCP endpoint at `https://rail.mikegtn.net/mcp`. The public MCP
connection does not require an end-user account or bearer token. Realtime Trains
credentials remain on the server and are used only by the private Action backend.

## Tools

The server currently exposes twelve MCP tools:

- `getNextDepartures`
- `searchStationServices`
- `getServiceDetails`
- `getTigerServiceDetails`
- `getRttApiInfo`
- `getApiUsage`
- `suggestRailRoute`
- `getJourneyRoute`
- `getRailMapSnapshot`
- `findJourneys`
- `getTrainLocation`
- `getRouteDetails`

Every tool declares a canonical top-level `securitySchemes: [{"type":"noauth"}]`
entry plus the documented `_meta.securitySchemes` compatibility mirror.

Tool calls return only the public result object as `structuredContent`. Internal
Action `requestEvidence` and composite source request IDs remain server-side and
are not returned to ChatGPT or Codex. Exact RTT `uniqueIdentity` values remain in
results because follow-up service, location and mapping calls need them.

The server publishes explicit output schemas for the model-facing result of every
tool. Upstream RTT objects that can legitimately evolve remain typed as bounded
nested objects, while the stable fields used by follow-up workflows are declared
explicitly.

## Important behaviour

- `findJourneys` searches direct trains and up to three caller-supplied
  interchange stations. It is bounded and not an exhaustive national journey
  planner.
- Minimum interchange times are not verified; the connection buffer is a search
  assumption.
- `getTrainLocation` returns the latest actual RTT timing report. It is not GPS
  and must not be presented as the train's guaranteed present position.
- Forecast timings are forecasts, not observations.
- Route geometry between schedule points can be inferred by the topology engine.
- Infrastructure routes are not timetables, tickets or guaranteed passenger
  itineraries.
- Snapshots are generated only when explicitly requested.

## Connection

The portable/local MCP configuration is deliberately credential-free:

```json
{
  "mcpServers": {
    "realtime-trains": {
      "type": "http",
      "url": "https://rail.mikegtn.net/mcp"
    }
  }
}
```

No RTT or server credential belongs in the plugin package, client configuration,
chat, or browser. `ACTION_API_KEY` remains available only to the server-side
sidecar so it can call the existing loopback Action API.

The public endpoint has a process-level abuse ceiling in addition to the Action
backend's own concurrency controls and the upstream RTT rate limits.

## Skills

The repository still contains the existing `realtime-trains` skill and preserved
references for local/package testing. Version 0.1.0 of the public directory
submission is intentionally **MCP-tools-only**. The public MCP endpoint therefore
does not advertise the draft MCP Skills extension or expose skill resources for
submission import.

This keeps the first public release on stable MCP behaviour. A later release can
add imported skills after the workflow and resource manifest are reviewed against
the then-current Skills extension.

## Run and deploy

Install the `mcp` extra and run:

```text
python -m rtt_app.mcp_server --transport http
```

The production sidecar listens on loopback port 8766. `ACTION_API_KEY` and
optionally `MCP_BACKEND_URL` configure only the sidecar-to-Action hop. Remote
backend URLs must use HTTPS.

`deploy/update_mcp.sh COMMIT_SHA` installs a pinned release in an isolated
virtual environment, runs the test suite, updates the systemd sidecar and Apache
`/mcp` proxy, removes the retired public OAuth proxy routes and consent page, and
runs the public MCP protocol verification. The old private OAuth database and key
are deliberately left on disk during the transition so a rollback remains
possible; they are no longer loaded by the service.

`deploy/verify_mcp.py --output /tmp/rtt-mcp-verification.json` verifies the real
HTTPS endpoint without credentials, confirms all tools publish `noauth`
security metadata and output schemas, checks that request evidence is absent from
public results, and exercises the Aberdeen-to-Plymouth service/location/map
workflow.

## Submission shape

Version 0.1.0 is intended to be submitted as:

- **With MCP**
- Universal MCP URL: `https://rail.mikegtn.net/mcp`
- Authentication: **None / public**
- Custom UI: none
- Imported MCP skills: none for the first release

The listing should visibly attribute Realtime Trains as required by the applicable
API terms and must not imply an official relationship beyond the permissions
actually granted.

## Official implementation references

- [Build an MCP server](https://developers.openai.com/plugins/build/mcp-server)
- [Authentication](https://developers.openai.com/plugins/build/auth)
- [Package a plugin](https://developers.openai.com/plugins/build/plugins)
- [Connect and test](https://developers.openai.com/plugins/deploy/connect-chatgpt)
