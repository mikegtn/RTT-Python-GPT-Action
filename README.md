# Realtime Trains API explorer

A small, dependency-free Python client and CLI for the current Realtime Trains
Next Generation API. It uses Bearer authentication, supports both access and
refresh tokens, exposes the main search endpoints, and reports rate-limit
headers when requested.

The API token must remain private. This project reads it from a git-ignored
`.env` file and does not embed it in Python source.

## Setup

Requires Python 3.10 or newer.

```powershell
Copy-Item .env.example .env
# Edit .env and replace RTT_TOKEN with the token displayed in the RTT portal.
python -m rtt_app.cli --show-rate-limits info
```

The UUID in the portal URL is not assumed to be the bearer token; copy the
actual access or refresh token displayed after authenticated login into `.env`.

## Windows desktop app

Double-click `RTT Departure Board.pyw` for a console-free Windows app. If `.pyw`
files are not associated with Python on the machine, double-click
`launch-rtt-gui.cmd` instead, or start it from PowerShell:

```powershell
python -m rtt_app.gui
```

Enter a station name such as `Clapham Junction`. The app shows scheduled and
expected times, destination, platform, allocation and service status. The token
field is masked and is pre-filled from `.env` when `RTT_TOKEN` is configured.
The GUI does not save a token entered into the field.

## GPT-powered Rail Assistant

Double-click `RTT Rail Assistant.pyw` to ask natural-language questions about
rail services. It connects GPT to the local RTT Python client using function
calling, so the RTT token remains on this machine rather than being placed in a
Custom GPT or browser client.

Add an OpenAI API key to `.env` (or paste it into the masked field):

```dotenv
OPENAI_API_KEY=replace-with-your-openai-api-key
OPENAI_MODEL=gpt-5.4
```

Example questions:

- `What are the next five trains from Bristol Temple Meads?`
- `What unit is working the 18:36 Paddington to Castle Cary today?`
- `Which arriving train forms it?`
- `Is it late or cancelled, and what platform is it using?`
- `Does RTT provide coach letters or First Class formation data?`

The assistant is instructed to query RTT for live claims, distinguish booked
from live data, and say when allocation or KYT information is absent. OpenAI API
usage is billed separately from ChatGPT subscriptions.

## ChatGPT GPT Action

The project also includes a read-only HTTP bridge for a GPT Action. In this
configuration ChatGPT supplies the language model, so the bridge does **not**
need `OPENAI_API_KEY`. It needs the RTT token plus a separate action password:

```dotenv
RTT_TOKEN=your-rtt-token
ACTION_API_KEY=generate-a-long-random-value
ACTION_BASE_URL=https://your-public-action-host.example
```

Generate an action password in PowerShell:

```powershell
$bytes = [byte[]]::new(32)
[Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
[Convert]::ToHexString($bytes)
```

Save the result as `ACTION_API_KEY`; do not reuse or expose the RTT token. Start
the service locally with `launch-rtt-action.cmd` or:

```powershell
.venv\Scripts\python -m rtt_app.action_api
```

Useful local URLs are `http://127.0.0.1:8765/health` and
`http://127.0.0.1:8765/openapi.json`. ChatGPT cannot call a loopback address, so
deployment or a tunnel must provide a stable public HTTPS origin. A `Dockerfile`
is included for a container host. Configure `RTT_TOKEN`, `ACTION_API_KEY`, and
`ACTION_BASE_URL` as host secrets; never copy `.env` into the image.

After deployment:

1. Open the GPT editor and create an action.
2. Import `https://YOUR-HOST/openapi.json`.
3. Select API key authentication, choose Bearer, and enter the same
   `ACTION_API_KEY` value.
4. Paste the contents of `GPT_ACTION_INSTRUCTIONS.md` into the GPT instructions.
5. Test `getRttApiInfo`, `getNextDepartures`, `searchStationServices`,
   `getServiceDetails`, and `suggestRailRoute` in the action test panel.

The bridge exposes only these authenticated, read-only routes:

- `GET /v1/departures`
- `GET /v1/services`
- `GET /v1/service`
- `GET /v1/info`
- `GET /v1/usage` — aggregate request counts by endpoint and response status
- `GET /v1/route` — topology-backed Movebook railway route, mileage, alternatives and map link

It also serves unauthenticated `/health`, `/openapi.json`, `/privacy`, and generated
`/maps/{id}` route-map pages. Set `MOVEBOOK_ROUTE_SCRIPT` to the Movebook
`scripts/route_movebook_draft.py` path and `ACTION_MAP_DIR` to a writable map cache.

Installing the local `rtt` command is optional:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e .
rtt info
```

## Examples

```powershell
# Look up a station by name and show its next five passenger trains
python -m rtt_app.cli next "Clapham Junction"

# Trains at London Waterloo for the default 60-minute window
python -m rtt_app.cli location WAT

# Two-hour detailed query (when the token is entitled)
python -m rtt_app.cli location CLJ --from 2026-08-13T14:00:00+01:00 --minutes 120 --detailed

# Only services subsequently calling at Waterloo
python -m rtt_app.cli location CLJ --filter-to WAT

# Fetch a service using uniqueIdentity from a location result
python -m rtt_app.cli service --unique-id L01525:2026-08-13

# Reference data, available from API version 2026-04-09 onward
python -m rtt_app.cli stops
python -m rtt_app.cli locations

# Entitlement-gated rolling-stock queries
python -m rtt_app.cli allocations-service 2026-08-13 SW
python -m rtt_app.cli allocations-class 2026-08-13 444

# Explore any future GET endpoint
python -m rtt_app.cli raw /gb-nr/location code=WAT timeWindow=30
```

The client defaults to Network Rail-specific endpoints because they include
useful GB railway fields such as headcodes where entitled. Add `--generic` to
location or service queries for namespace-generic output.

## API surface mapped

- `GET /api/info`: version, namespaces, history limits and entitlements
- `GET /api/get_access_token`: exchange a refresh token for an access token
- `GET /rtt/location`, `GET /rtt/service`: namespace-generic data
- `GET /gb-nr/location`, `GET /gb-nr/service`: Network Rail-specific data
- `GET /data/stops`, `GET /data/locations_ungrouped`: reference data
- `GET /gb-nr/allocations/by-service`, `/by-class`: gated allocations

## Tests

```powershell
python -m unittest discover -v
```

Tests mock the network and never require or expose a real token.

## Official documentation

- <https://realtimetrains.github.io/api-specification/>
- <https://github.com/realtimetrains/api-specification>
- <https://api-portal.rtt.io/>

## TIGER coach integration

`GET /v1/tiger/service` (`getTigerServiceDetails`) uses the existing Action
Bearer or `X-Action-Key` authentication. Required parameters: `station` and `uid` (the exact RTT UID). TIGER requires
a TIPLOC, such as `PADTON`, rather than the RTT CRS code `PAD`. A CRS `station`
is resolved from the selected RTT service when `unique_identity` is supplied.
Alternatively supply `tiploc=PADTON` explicitly alongside `station=PAD`.
Ambiguous or unresolved CRS mappings return 400; they are never guessed. Optional `departure_date` (`YYYY-MM-DD`)
and `unique_identity` (copied from RTT) enable explicit date checking and RTT
reconciliation. The response is `{ok, result}`; supplying `unique_identity`
returns separate `rtt` and `tiger` evidence plus authority, conflicts and warnings.

Set `TIGER_API_KEY` only in the server environment or `/etc/rtt-action.env`.
Optional settings are `TIGER_BASE_URL` (default
`https://tiger-api-portal.worldline.global`) and `TIGER_TIMEOUT` (15 seconds,
maximum 60). Changing the base URL sends the credential to that configured
HTTPS host, so use only a trusted provider endpoint. The client sends the key
in `x-api-key`, never a URL, and refuses redirects. Error bodies are not returned.
Without the key, only the TIGER endpoint is disabled (503).

The client fetches `/services/{station}` and selects exactly one `UID` match.
No match returns 404, duplicate matches 409, and upstream failures or malformed
payloads 502. TIGER HTTP-200 `{name: "NotFound", detail: ...}` responses are
translated to a safe 404; other application errors return 502 without echoing
upstream detail. It accepts a service array or `Services`/`services` wrapper.
These envelope adapters and the optional `DepartureDate` field still need
validation against an authenticated live response; no date is invented when
that field is absent. Tests use synthetic fixtures derived from the agreed
CoachList example, not a captured live response.

Coach normalization preserves `rawService` and `rawCoachList`, sorts unique
positive `CoachNumber` values, and uses consistent `LeadingPowerCar` or
`TrailingPowerCar` end markers for front-to-rear order. Absent, conflicting or
internal markers leave orientation unknown. Missing facilities remain null
("not indicated"). Empty/missing lists do not mean a zero-coach train.

Reconciliation requires exact dated RTT identity and station membership.
Unverified dates never produce confirmed enrichment. RTT data remains intact;
TIGER never replaces operational fields or allocation identities. A mismatch
between the station's RTT `passengerVehicles` and TIGER coach count is reported
with both values and blocks confirmed enrichment. Other raw upstream fields
remain available as evidence; undocumented fields are not automatically merged
or interpreted as operational conflicts.

### Updating the existing systemd deployment

The existing service runs from `/opt/rtt-action`, loading `/etc/rtt-action.env`.
Do **not** rerun `deploy/install.sh` for an upgrade: it is a first-install script.
Deploy the reviewed commit through the server's existing release process, add
`TIGER_API_KEY` to the protected environment file without printing it, then:

```sh
cd /opt/rtt-action
python3 -m unittest discover -v
sudo systemctl restart rtt-action.service
sudo systemctl is-active rtt-action.service
sudo python3 deploy/verify_tiger.py --station PAD --tiger-station PADTON
```

The verification command checks the published OpenAPI operation, discovers an
exact live RTT UID at the station, then requests TIGER evidence. It reads the
Action key from the environment file into memory and prints only safe summary
fields. It never prints either key or upstream error bodies. It exits nonzero
if no candidate has coach data; try a station served by an operator publishing
formations. A successful response with `dateVerified=false` verifies the
transport and coach extraction only, not a dated RTT reconciliation.

For the existing `/opt/rtt-action` deployment, `deploy/update_tiger.sh COMMIT_SHA`
stages a pinned release, runs its tests, backs up the existing installation,
creates the deployment directory if absent, installs the TIGER-related Python
files and restarts the service. It restores the backup if installation or the
local health check fails. It preserves `/etc/rtt-action.env` and never prints
credentials. Run the live verifier separately after the update: `--station`
selects the RTT station; `--tiger-station` explicitly selects its TIGER TIPLOC.
Omit `--tiger-station` to test automatic resolution from RTT calls.
