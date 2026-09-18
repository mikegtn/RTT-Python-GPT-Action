"""Run on the deployment host; credentials remain in process memory."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None



def safe_api_error(error, config):
    """Expose only the API error field, with configured secrets removed."""
    try:
        payload = json.loads(error.read(65537))
        detail = payload.get('error') if isinstance(payload, dict) else None
        if not isinstance(detail, str):
            return 'No structured API error was returned.'
        for name, value in config.items():
            if any(word in name.upper() for word in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD')) and value:
                detail = detail.replace(value, '[REDACTED]')
        return ''.join(c if c.isprintable() else ' ' for c in detail)[:500]
    except (ValueError, OSError):
        return 'No structured API error was returned.'
    finally:
        error.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--station', default='PAD', help='RTT station name or CRS code')
    parser.add_argument('--tiger-station', help='Explicit TIGER TIPLOC, e.g. PADTON; otherwise resolve from RTT calls')
    parser.add_argument('--env-file', type=Path, default=Path('/etc/rtt-action.env'))
    args = parser.parse_args()
    config = dict(os.environ)
    if args.env_file.is_file():
        for line in args.env_file.read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key, value = line.split('=', 1)
                config.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    key = config.get('ACTION_API_KEY')
    if not key:
        sys.exit('ACTION_API_KEY is unavailable; no authenticated checks performed.')
    # Fixed production origin prevents configuration from redirecting credentials.
    origin = 'https://rail.mikegtn.net'
    opener = build_opener(NoRedirect())
    def get(path, params=None, authenticated=True):
        url = origin + path + ('?' + urlencode(params) if params else '')
        headers = {'Accept': 'application/json'}
        if authenticated:
            headers['Authorization'] = 'Bearer ' + key
        try:
            with opener.open(Request(url, headers=headers), timeout=25) as response:
                return json.load(response)
        except HTTPError as error:
            error.safe_detail = safe_api_error(error, config)
            raise
    schema = get('/openapi.json', authenticated=False)
    operation = schema.get('paths', {}).get('/v1/tiger/service', {}).get('get', {})
    if operation.get('operationId') != 'getTigerServiceDetails':
        sys.exit('Published schema does not include getTigerServiceDetails.')
    print('OpenAPI: getTigerServiceDetails present')
    board = get('/v1/services', {'station': args.station, 'minutes': 180, 'count': 40})
    candidates = board.get('result', {}).get('services', [])
    attempted = 0
    for candidate in candidates:
        identity = (candidate.get('scheduleMetadata') or {}).get('uniqueIdentity', '')
        match = re.fullmatch(r'(?:gb-nr:)?([A-Z][0-9]{5}):(\d{4}-\d{2}-\d{2})', identity)
        if not match:
            continue
        attempted += 1
        try:
            result = get('/v1/tiger/service', {'station': args.station, 'uid': match[1],
                         'departure_date': match[2], 'unique_identity': identity,
                         **({'tiploc': args.tiger_station} if args.tiger_station else {})})
        except HTTPError as error:
            code = error.code
            error.close()
            if code == 404 and attempted < 10:
                continue
            sys.exit(f'TIGER verification failed: HTTP {code}: {error.safe_detail}')
        reconciled = result.get('result', {})
        tiger = reconciled.get('tiger', {})
        if result.get('ok') and tiger.get('coaches'):
            print(json.dumps({'uid': match[1], 'rttStation': args.station, 'tigerStation': tiger.get('station'),
                  'totalCoaches': tiger.get('totalCoaches'),
                  'orientationKnown': tiger.get('orientationKnown'),
                  'dateVerified': tiger.get('dateVerified'),
                  'coachEnrichmentApplied': reconciled.get('coachEnrichmentApplied')}))
            return
        if attempted >= 10:
            break
    sys.exit('No live RTT candidate with TIGER coach data found (at most ten checked).')


if __name__ == '__main__':
    try:
        main()
    except HTTPError as error:
        code = error.code
        error.close()
        sys.exit(f'Verification failed: HTTP {code}: {getattr(error, "safe_detail", "No diagnostic available")}')
    except (URLError, OSError, ValueError, TypeError):
        sys.exit('Verification failed: unavailable service or invalid response; credentials suppressed.')
