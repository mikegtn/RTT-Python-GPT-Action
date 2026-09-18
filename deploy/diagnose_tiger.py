"""Read-only station/date diagnostics. Never print raw responses or credentials."""
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

LIMIT = 2_000_000
SENSITIVE = re.compile(r'key|token|secret|password|authorization|credential', re.I)
DATE_VALUE = re.compile(r'(?:\d{4}-\d{2}-\d{2}(?:[T ].*)?|\d{8}|\d{1,2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?)')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def date_evidence(value, path='$', depth=0):
    """Collect date/time-shaped scalars without dumping other service data."""
    found = []
    if depth > 6:
        return found
    if isinstance(value, dict):
        for name, child in list(value.items())[:150]:
            if SENSITIVE.search(name) or name == 'CoachList':
                continue
            child_path = path + '.' + name
            if isinstance(child, str) and DATE_VALUE.fullmatch(child):
                found.append({'path': child_path, 'value': child[:100]})
            elif re.search(r'date|time|timestamp', name, re.I) and isinstance(child, (int, float)) and not isinstance(child, bool):
                found.append({'path': child_path, 'value': child})
            else:
                found.extend(date_evidence(child, child_path, depth + 1))
    elif isinstance(value, list):
        for i, child in enumerate(value[:3]):
            found.extend(date_evidence(child, f'{path}[{i}]', depth + 1))
    return found[:60]


def fields(value):
    return {k: type(v).__name__ for k, v in value.items() if not SENSITIVE.search(k)} if isinstance(value, dict) else type(value).__name__


def main():
    config = dict(os.environ)
    env_file = Path('/etc/rtt-action.env')
    if env_file.is_file():
        for line in env_file.read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key, value = line.split('=', 1)
                config.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    if not all(config.get(k, '').strip() for k in ('ACTION_API_KEY', 'TIGER_API_KEY')):
        sys.exit('Required server-side credentials are missing; no values printed.')
    secrets = [v for k, v in config.items() if SENSITIVE.search(k) and v]
    def emit(value):
        # Redact before encoding, including unusual characters within secrets.
        def scrub(item):
            if isinstance(item, str):
                for secret in secrets:
                    item = item.replace(secret, '[REDACTED]')
                return ''.join(c if c.isprintable() else ' ' for c in item)
            if isinstance(item, list):
                return [scrub(x) for x in item]
            if isinstance(item, dict):
                return {scrub(k): scrub(v) for k, v in item.items()}
            return item
        print(json.dumps(scrub(value), ensure_ascii=False))
    opener = build_opener(NoRedirect())
    def get(origin, path, headers, params=None):
        url = origin + path + ('?' + urlencode(params) if params else '')
        try:
            with opener.open(Request(url, headers=headers), timeout=25) as response:
                raw = response.read(LIMIT + 1)
                if len(raw) > LIMIT:
                    raise ValueError('oversized response')
                return json.loads(raw)
        except HTTPError as error:
            code = error.code
            error.close()
            raise RuntimeError(f'HTTP {code}') from None
    action_headers = {'Accept': 'application/json', 'Authorization': 'Bearer ' + config['ACTION_API_KEY'].strip()}
    tiger_headers = {'Accept': 'application/json', 'Origin': 'https://tiger.worldline.global',
                     'x-api-key': config['TIGER_API_KEY'].strip()}
    for crs, tiploc in [('PAD', 'PADTON'), ('BRI', 'BRSTLTM')]:
        identities = set()
        emit({'section': 'RTT station calls', 'crs': crs})
        try:
            board = get('https://rail.mikegtn.net', '/v1/services', action_headers,
                        {'station': crs, 'minutes': 180, 'count': 8})
            for candidate in board.get('result', {}).get('services', [])[:8]:
                identity = (candidate.get('scheduleMetadata') or {}).get('uniqueIdentity', '')
                match = re.fullmatch(r'(?:gb-nr:)?([A-Z][0-9]{5}):(\d{4}-\d{2}-\d{2})', identity)
                if not match:
                    continue
                identities.add(match[1])
                try:
                    detail = get('https://rail.mikegtn.net', '/v1/service', action_headers,
                                 {'unique_identity': identity}).get('result', {})
                    calls = []
                    for call in detail.get('calls') or []:
                        location = call.get('location') or {}
                        codes = (location.get('shortCodes') or []) + (location.get('longCodes') or [])
                        if crs in codes or tiploc in codes or (crs == 'PAD' and 'paddington' in str(location.get('description', '')).lower()):
                            calls.append({k: location.get(k) for k in ('description', 'shortCodes', 'longCodes')})
                    emit({'rttIdentity': identity, 'matchingCalls': calls})
                except (RuntimeError, ValueError, TypeError, AttributeError, OSError):
                    emit({'rttIdentity': identity, 'error': 'Service details unavailable or malformed'})
        except (RuntimeError, ValueError, TypeError, AttributeError, OSError):
            emit({'error': 'RTT board unavailable or malformed'})
        emit({'section': 'TIGER date evidence', 'tiploc': tiploc})
        try:
            payload = get('https://tiger-api-portal.worldline.global', '/services/' + tiploc, tiger_headers)
            emit({'envelopeFields': fields(payload)})
            if isinstance(payload, dict):
                metadata = {k: v for k, v in payload.items() if k not in ('Services', 'services')}
                emit({'envelopeDates': date_evidence(metadata)})
                services = payload.get('Services', payload.get('services'))
            else:
                services = payload
            if not isinstance(services, list):
                emit({'error': 'No supported service list; no raw values printed'})
                continue
            services = [s for s in services if isinstance(s, dict)]
            selected = [s for s in services if s.get('UID') in identities]
            selected += [s for s in services if s.get('CoachList') and s not in selected]
            for item in selected[:5]:
                uid = item.get('UID')
                emit({'uid': uid if isinstance(uid, str) and re.fullmatch(r'[A-Z][0-9]{5}', uid) else None,
                      'matchesRttBoardUid': uid in identities if isinstance(uid, str) else False,
                      'serviceFields': fields(item), 'dateEvidence': date_evidence(item),
                      'hasCoachList': bool(item.get('CoachList'))})
            if not selected:
                emit({'serviceCount': len(services), 'note': 'No matching UID or CoachList sample available'})
        except (RuntimeError, ValueError, TypeError, AttributeError, OSError):
            emit({'error': 'TIGER data unavailable or malformed; no raw values printed'})


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, TypeError, URLError):
        sys.exit('Diagnostic could not complete; credentials suppressed.')
