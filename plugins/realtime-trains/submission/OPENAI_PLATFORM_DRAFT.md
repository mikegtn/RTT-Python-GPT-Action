# Realtime Trains — OpenAI Platform submission draft

**Prepared:** 26 September 2026
**Target MCP endpoint:** `https://rail.mikegtn.net/mcp`
**Submission route:** With MCP
**Draft status:** Source remediation applied with the latest TrainBrain features on `codex/trainbrain-publication`; deploy this branch, run the authoritative Platform **Scan Tools**, and complete the remaining listing/domain items before submission.

This file is the copy-ready content for the OpenAI Platform submission form. It is not evidence that a draft has already been saved in the Platform portal.

## 1. Listing details

| Field | Draft value |
|---|---|
| Plugin name | TrainBrain |
| Developer / publisher | Mike Newman — must match the verified Platform publisher identity |
| Category | Productivity |
| Initial country availability | United Kingdom |
| MCP URL type | Universal |
| MCP URL | `https://rail.mikegtn.net/mcp` |
| Authentication | None — public MCP endpoint |
| Tool security | `securitySchemes: [{"type":"noauth"}]` on every tool, mirrored in `_meta` |
| Website | `https://rail.mikegtn.net` |
| Privacy policy | `https://rail.mikegtn.net/privacy` — verify that this resolves publicly before submission |
| Support URL | **REQUIRED:** add a public HTTPS support/contact page |
| Terms of service | **REQUIRED:** add a public HTTPS terms page |
| Repository | `https://github.com/mikegtn/RTT-Python-GPT-Action` |
| Custom UI | None in version 0.1.0 |
| Imported MCP skills | None in version 0.1.0; first public release is MCP-tools-only |
| Logo / icon | **REQUIRED:** upload final artwork and confirm rights |
| Demo recording | **REQUIRED:** add a public or reviewer-accessible video URL |

### Name and rights check

TrainBrain is the independent assistant name; Realtime Trains is the data provider attribution. Before public submission, confirm that the publisher has permission to use the Realtime Trains name, data and branding in a public third-party plugin and that the listing does not imply an official relationship beyond the permission actually held.

## 2. Listing copy

### Short description

Live and scheduled UK rail services, train progress, journey options and route maps.

### Long description

Use TrainBrain with Realtime Trains data to search dated UK rail services, inspect an exact train's schedule and latest actual timing report, check formations and facilities when the source supplies them, compare bounded journeys with up to three changes, and generate route maps from verified service identities.

Rail data can change or be unavailable. A train location is based on its latest actual timing report and is not GPS. Journey searches are bounded, are not exhaustive, and do not verify official minimum interchange times. The plugin does not sell tickets or guarantee that a connection is valid.

### Release notes — version 0.1.0

Initial read-focused release providing station departures, dated service search, exact service details, formations and facilities where supplied, latest actual timing reports, bounded journey options, infrastructure routes, verified itinerary maps and optional static map snapshots and actual-report-based service progress schematics.

## 3. Starter prompts

1. Show the next five departures from Bristol Temple Meads.
2. Find journey options from London Paddington to Plymouth tomorrow after 08:00.
3. Where was this train last reported, and how old is that report?
4. Show the calling points, formation and available facilities for this service.
5. Generate an interactive route map for the exact train I selected.
6. Create a static whole-route image of that map.

## 4. Capability boundaries shown to users and reviewers

- Preserve each exact RTT `uniqueIdentity`; never construct or substitute one.
- Treat latest train location as the last actual timing report, not GPS.
- Describe forecasts as forecasts, not observations.
- `findJourneys` covers direct journeys and up to three changes among supplied candidate stations and returns at most three options from bounded searches.
- Minimum interchange times are not verified.
- Infrastructure routes are not timetables, tickets or guaranteed passenger itineraries.
- Generate a static snapshot only when the user explicitly asks for an image or snapshot.
- Do not expose credentials, bearer tokens, internal request traces or server diagnostics.
- Public MCP tool results contain only the declared result object; backend `requestEvidence` remains server-side.

## 5. Tool inventory and annotation justifications

| Tool | Purpose | readOnlyHint | destructiveHint | idempotentHint | openWorldHint | Justification |
|---|---|---:|---:|---:|---:|---|
| `getNextDepartures` | Next passenger departures from a station | true | false | true | true | Reads current external rail data and makes no user-account changes. |
| `searchStationServices` | Search dated arrivals or departures | true | false | true | true | Reads external timetable/live data only. |
| `getTigerServiceDetails` | Retrieve TIGER-backed service and formation details | true | false | true | true | Reads external service information only. |
| `getServiceDetails` | Retrieve one exact service | true | false | true | true | Reads the service identified by an opaque identity. |
| `getRttApiInfo` | Read API version and entitlement metadata | true | false | true | true | Diagnostic read with no mutation. Consider excluding from the public tool set if it is not useful to end users. |
| `getApiUsage` | Read aggregate hosted API usage | true | false | true | true | Aggregate diagnostic read. Consider excluding from the public tool set because it is publisher-facing rather than user-facing. |
| `suggestRailRoute` | Calculate a topology-backed infrastructure route and save a map | false | false | false | true | Creates a server-side map artifact but does not alter user data or external railway systems. |
| `getJourneyRoute` | Build a map for verified dated service legs | false | false | false | true | Creates a server-side map artifact from exact services. |
| `getRailMapSnapshot` | Render a saved route as a PNG | false | false | false | true | Creates a server-side image artifact from an existing map. |
| `findJourneys` | Search bounded passenger journey options | true | false | true | true | Reads live/scheduled services and performs bounded computation only. |
| `getTrainLocation` | Return the latest actual timing report | true | false | true | true | Reads one service and reports the last observation; it is not GPS. |
| `getRouteDetails` | Return ordered timing points for one service | true | false | true | true | Reads the exact service and returns its ordered calls and timing points. |

| `getServiceProgress` | Actual-report-based progress with optional PNG | false | false | false | true | May create a public image artifact on explicit request; does not alter railway systems. |
| `getServiceSchematic` | On-demand service progress PNG | false | false | false | true | Creates a public schematic image from exact service reports. |

## 6. Positive review test cases — exactly five

### Positive 1 — live departures

**Prompt:** Show the next five departures from Bristol Temple Meads.

**Expected behaviour:** Resolve the station unambiguously, call `getNextDepartures` with a count of five, present services in departure order, preserve any returned service identities for follow-up, and distinguish scheduled, forecast and actual times.

### Positive 2 — bounded journey search

**Prompt:** Find journey options from London Paddington to Plymouth tomorrow after 08:00.

**Expected behaviour:** Call `findJourneys` with an explicit ISO datetime including a UTC offset. Return no more than three supported options, disclose the bounded-search coverage and the fact that official minimum interchange times are not verified, and avoid claiming that the options are exhaustive or optimal.

### Positive 3 — exact service details and formation

**Conversation setup:** Select a service returned by Positive 1 or Positive 2.

**Prompt:** Show the calling points, formation and facilities for that exact train.

**Expected behaviour:** Reuse the exact returned `uniqueIdentity`, call `getServiceDetails`, and report only formation or facility information actually present. Do not construct a new identity or infer missing stock details.

### Positive 4 — latest reported location

**Conversation setup:** Continue with the exact service selected in Positive 3.

**Prompt:** Where was this train last reported, and how old is that report?

**Expected behaviour:** Call `getTrainLocation` with the unchanged identity. State the location, event and report time when available; explicitly say this is the latest actual timing report rather than GPS and warn that the train may have moved.

### Positive 5 — exact journey map and snapshot

**Conversation setup:** Continue with one or more exact service legs returned by Positive 2.

**Prompt:** Generate an interactive route map for these exact trains, then create a whole-route static image.

**Expected behaviour:** Call `getJourneyRoute` with the exact ordered identities and station endpoints, then call `getRailMapSnapshot` using the returned map ID. Preserve the interactive map link, show the static image URL, and retain warnings about inferred geometry and unverified interchange times.

## 7. Negative review test cases — exactly three

### Negative 1 — unknown station

**Prompt:** Show departures from `ZZZQ NOT A STATION`.

**Expected behaviour:** Return a clear station-resolution or upstream error. Do not silently substitute a similarly named station or invent departures.

### Negative 2 — fabricated service identity

**Prompt:** Show details for unique identity `made-up-service-123`.

**Expected behaviour:** Reject or return not found for the supplied identity. Do not select a different service, reconstruct an identity, or manufacture calling points.

### Negative 3 — unsupported exhaustive journey request

**Prompt:** Find every possible journey from Thurso to Penzance and guarantee the fastest valid connections.

**Expected behaviour:** Explain the bounded coverage of direct journeys and up to three changes among supplied candidate stations and that official minimum interchange times are not verified. The plugin may provide supported candidates but must not claim exhaustive coverage, fastest status or guaranteed connections.

## 8. Reviewer notes

- The endpoint uses Streamable HTTP at `/mcp`.
- The MCP endpoint is intentionally public and requires no end-user authentication.
- Every production tool is intended to be non-destructive.
- Three map tools create temporary/server-side artifacts and are therefore deliberately not marked read-only or idempotent.
- Live railway data can change between calls.
- The service's exact opaque identities must remain unchanged through detail, location and mapping workflows.
- Version 0.1.0 is intentionally MCP-tools-only; the public endpoint does not advertise the draft MCP Skills extension.

## 9. Manual portal items still required

- Select the verified publisher identity and eligible Platform project.
- Confirm the project is eligible for public plugin submission.
- Enter the universal MCP URL and select **No authentication / public**.
- Run the portal's **Scan Tools** action and save its actual result.
- Enter a justification for each annotation the portal asks about.
- Complete the portal-issued domain challenge.
- Add support and terms URLs.
- Add final icon/logo assets.
- Add the demo recording URL.
- Confirm the deployed tool list shows top-level and `_meta` `noauth` security schemes and result-only output schemas.
- Confirm data-source and brand authorisation.
- Submit only after every blocker in the scan report is closed.
