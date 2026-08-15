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
