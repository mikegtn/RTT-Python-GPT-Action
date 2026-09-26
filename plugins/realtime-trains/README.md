# TrainBrain

This package exposes TrainBrain, an independent assistant using Realtime Trains data, through the public,
read-only MCP endpoint at `https://rail.mikegtn.net/mcp`. The public MCP
connection does not require an end-user account or bearer token. Realtime Trains
credentials remain on the server and are used by the direct railway backend.

## Tools

The server currently exposes fourteen MCP tools:

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

- `getServiceSchematic`: first-class PNG progress tool. Accepts the exact
  `unique_identity` and optional `as_of`; always requests an image and returns
  native MCP image content, a public PNG URL, progress based on actual movement reports.
  Omit `as_of` for live requests. The connected plugin is the preferred user-facing
  interface. Railway operations execute directly inside the MCP process.

- `getServiceProgress`: actual-report-based `not_started`, `at_station`,
  `between_calls` and `completed` states for an exact `unique_identity`.
  Optional offset-aware `as_of` is an inclusive historical event cutoff.
  Returns station endpoints, actual and scheduled times, RTT lateness, an explicit
  non-GPS basis. Forecasts and interpolated reports cannot
  establish movement. Conflicting or insufficient evidence produces an error.
  Historical replay uses today's retrieved record, not historical receipt times
  or former timetable/cancellation revisions. Run the live regression with
  `python deploy/verify_mcp.py --service-progress --output progress.json`.
  Set `include_image=true` on explicit request to return an inline MCP PNG and
  `imageUrl` for a schematic of all passenger stops. The highlighted station or
  segment comes from the same progress record; the train symbol never indicates
  distance along the line. Future actuals are hidden at historical cutoffs.
  Cancelled stops remain labelled in service order. Repeated locations use exact
  call indices. Actual times more than 60 seconds after the matching advertised
  time are red; within tolerance or early reports are teal. Completed segments use
  the far-end actual arrival (departure fallback); future/unknown segments remain
  grey. The current reported segment or station has a soft glow in its timing
  colour. No forecast is used to colour a completed section.
  `imageError` preserves the progress result if rendering fails.
  The optional Pillow extra is required; set `MCP_PROGRESS_IMAGE_DIR` to a writable
  persistent directory (configured by the MCP service unit). Completed PNGs are
  public at unguessable `/mcp/progress/<id>.png` URLs; generation is subject to the public endpoint limits. Storage retains up to 512 images for at most seven days.
  Add `--progress-image` to the regression command to verify all four images,
  compare native image bytes with unauthenticated public downloads, and save PNGs.

- `findJourneys`: direct trains and up to three changes among three caller-supplied
  candidate interchange stations, explored in any order. `max_changes` can limit
  changes to 0-3 (default 3). Six candidates per board, 18 partial journeys per
  depth and 96 backend requests bound the search; up to three options are returned.
  Each connection reports scheduled and available actual/forecast gaps, with
  warnings for insufficient live buffers. Arrivals are limited to 36 hours after
  search start; `minutes` limits only the initial departure window. Uses advertised
  times, passenger call restrictions, cancellations and chronological checks.
  It is not an exhaustive national journey planner. A connection buffer is an
  assumption, not a sourced minimum interchange time.
- `getTrainLocation`: last actual RTT timing report, its timestamp and age.
  Never GPS and never a forecast presented as an observed position.
- `getRouteDetails`: ordered service-detail calls/timing points, without
  claiming geometry, distance, or a complete technical schedule.
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
chat, or browser. `ACTION_API_KEY` continues to protect the separate GPT Action.
MCP executes railway operations directly and does not call the Action HTTP API.

The public endpoint has a process-level abuse ceiling alongside bounded MCP
execution concurrency and the upstream RTT rate limits.

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

From the repository, install the `mcp` extra and run
`python -m rtt_app.mcp_server --transport http` (loopback port 8766) or use
`--transport stdio`. `RTT_TOKEN` configures direct RTT access; optional TIGER
credentials enable coach details. MCP requires no client authentication. There is no request
to the Action web service and `MCP_BACKEND_URL` is no longer used.

Both adapters share `rtt_app/railway_service.py`. MCP owns `MCP_USAGE_FILE` and
`MCP_MAP_DIR`, defaulting to `/var/lib/rtt-mcp/usage.json` and
`/var/lib/rtt-mcp/maps`; set writable paths when running locally. Its
`MCP_ASSET_BASE_URL` defaults to `https://rail.mikegtn.net/mcp/assets`, which serves
its route maps, snapshots and coach icons. Existing Action URLs and counters
remain unchanged; MCP's new counter starts independently.

The production unit reuses the existing protected environment file for upstream
credentials. Route geometry still uses `MOVEBOOK_ROUTE_SCRIPT` from Lost::MikeGTN2;
the retired OAuth consent bridge is removed with a rollback backup.
The Action process is not a startup or runtime dependency.
No OpenAI API key is needed: this server provides tools, not model inference.

```text
python -m rtt_app.mcp_server --transport http
```

The production MCP service listens on loopback port 8766 behind the HTTPS proxy.

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

The display name is TrainBrain. Existing package IDs and skill resource URIs retain
`realtime-trains` for connection compatibility. Realtime Trains remains the data
provider attribution, rather than the name of this independent assistant.
