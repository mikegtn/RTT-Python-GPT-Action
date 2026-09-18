"""TIGER passenger-coach evidence; RTT remains the operational authority."""
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

DEFAULT_BASE_URL = 'https://tiger-api-portal.worldline.global'
MAX_BYTES = 2_000_000


class TigerError(RuntimeError):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


class _NoRedirect(HTTPRedirectHandler):
    # Never forward x-api-key to a redirect target.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_lookup(station: str, uid: str, departure_date: str | None = None):
    station = station.strip().upper()
    if not re.fullmatch(r'[A-Z0-9]{3,7}', station):
        raise ValueError('station must be a CRS or TIPLOC code')
    if not re.fullmatch(r'[A-Z][0-9]{5}', uid):
        raise ValueError('uid must be the exact six-character RTT UID')
    if departure_date is not None:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', departure_date):
            raise ValueError('departure_date must be YYYY-MM-DD')
        date.fromisoformat(departure_date)
    return station



def resolve_tiger_tiploc(station: str, rtt: dict[str, Any] | None = None,
                         tiploc: str | None = None) -> str:
    """Resolve CRS using the selected RTT service, never by guessing a mapping.

    An explicit TIPLOC also supports short TIPLOCs or missing RTT reference data.
    If RTT supplies both codes, reject an explicit code for a different call.
    """
    station = validate_lookup(station, 'A00000')
    explicit = validate_lookup(tiploc, 'A00000') if tiploc else None
    matches = []
    for call in (rtt or {}).get('calls') or []:
        location = call.get('location') or {}
        short = location.get('shortCodes') or []
        long = location.get('longCodes') or []
        if station in short + long:
            matches.append(long)
    candidates = {code for codes in matches for code in codes
                  if isinstance(code, str) and re.fullmatch(r'[A-Z0-9]{3,7}', code)}
    if explicit:
        if candidates and explicit not in candidates:
            raise ValueError('tiploc does not match the requested RTT station')
        if not matches and len(station) > 3 and explicit != station:
            raise ValueError('tiploc conflicts with the supplied station TIPLOC')
        return explicit
    if len(candidates) == 1:
        return next(iter(candidates))
    if len(candidates) > 1:
        raise ValueError('Multiple TIPLOCs match this RTT station; supply tiploc explicitly')
    if len(station) > 3:
        return station
    raise ValueError('TIGER requires a TIPLOC: supply tiploc, or unique_identity so RTT can resolve the CRS code')


def _flag(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        return {'true': True, 'false': False, '1': True, '0': False}.get(value.lower().strip())
    return None


def normalize_coach_list(raw: Any) -> dict[str, Any]:
    """Sort by CoachNumber, and orient only with unambiguous end markers.

    Null flags mean not indicated. Letters and class never imply direction.
    Invalid/duplicate numbers preserve input order but disable orientation.
    """
    if raw is None:
        raw = []
    if not isinstance(raw, list) or any(not isinstance(c, dict) for c in raw):
        raise TigerError('TIGER returned an invalid CoachList')
    fields = ('LeadingPowerCar', 'TrailingPowerCar', 'FirstClass', 'StandardClass',
              'Wheelchairs', 'BikeStorage', 'Catering')
    coaches = []
    for c in raw:
        number = c.get('CoachNumber')
        if isinstance(number, str) and number.isdigit():
            number = int(number)
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            number = None
        coaches.append({'coachNumber': number, 'coachLetter': c.get('CoachLetter') if isinstance(c.get('CoachLetter'), str) else None,
                        **{f[0].lower() + f[1:]: _flag(c.get(f)) for f in fields}})
    numbers = [c['coachNumber'] for c in coaches]
    ordered = bool(coaches) and None not in numbers and len(set(numbers)) == len(numbers)
    if ordered:
        coaches.sort(key=lambda c: c['coachNumber'])
    leading = [i for i, c in enumerate(coaches) if c['leadingPowerCar'] is True]
    trailing = [i for i, c in enumerate(coaches) if c['trailingPowerCar'] is True]
    last = len(coaches) - 1
    forward = (leading == [0] or trailing == [last]) and not (leading and leading != [0]) and not (trailing and trailing != [last])
    reverse = (leading == [last] or trailing == [0]) and not (leading and leading != [last]) and not (trailing and trailing != [0])
    known = ordered and (forward or reverse)
    if known and reverse and not forward:
        coaches.reverse()
    warnings = []
    if coaches and not known:
        warnings.append('Orientation unknown: no unambiguous end markers and coach order.')
    def labels(field):
        return [c['coachLetter'] for c in coaches if c[field] is True and c['coachLetter'] is not None]
    return {
        'totalCoaches': len(coaches) if raw else None,
        'orientationKnown': bool(known),
        'frontCoach': coaches[0]['coachLetter'] if known else None,
        'rearCoach': coaches[-1]['coachLetter'] if known else None,
        'firstClassCoaches': labels('firstClass'),
        'wheelchairCoaches': labels('wheelchairs'),
        'bikeCoaches': labels('bikeStorage'),
        'cateringCoaches': labels('catering'),
        'coaches': coaches, 'rawCoachList': raw, 'warnings': warnings,
    }



def service_date_evidence(service: dict[str, Any]) -> dict[str, Any]:
    """Use scheduled origin dates, never station, forecast or message dates.

    Different origin dates on joined portions cannot establish one service date.
    Missing/malformed origin evidence also stays unverified.
    """
    evidence = []
    dates = set()
    invalid = False
    explicit = service.get('DepartureDate')
    if explicit not in (None, ''):
        try:
            if not isinstance(explicit, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', explicit):
                raise ValueError
            dates.add(date.fromisoformat(explicit).isoformat())
            evidence.append({'path': 'DepartureDate', 'value': explicit})
        except ValueError:
            invalid = True
    origins = service.get('Origins')
    if origins is not None:
        if not isinstance(origins, dict):
            invalid = True
        else:
            for portion, origin in origins.items():
                if origin in (None, {}, ''):
                    continue
                stamp = origin.get('DepTimestamp') if isinstance(origin, dict) else None
                try:
                    if not isinstance(stamp, str) or not re.match(r'^\d{4}-\d{2}-\d{2}T', stamp):
                        raise ValueError
                    parsed = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
                    if parsed.utcoffset() is None:
                        raise ValueError
                    dates.add(parsed.astimezone(ZoneInfo('Europe/London')).date().isoformat())
                    evidence.append({'path': f'Origins.{portion}.DepTimestamp', 'value': stamp})
                except ValueError:
                    invalid = True
    resolved = next(iter(dates)) if len(dates) == 1 and not invalid else None
    return {'departureDate': resolved,
            'dateMatchBasis': ('scheduledOriginDeparture' if any(e['path'].startswith('Origins.') for e in evidence)
                               else 'explicitDepartureDate') if resolved else None,
            'dateEvidence': evidence,
            'warnings': ['Origin date evidence is missing, malformed or conflicting.'] if invalid or len(dates) > 1 else []}


class TigerClient:
    def __init__(self, api_key: str, *, base_url: str = DEFAULT_BASE_URL, timeout: float = 15):
        if not api_key.strip():
            raise ValueError('TIGER_API_KEY is required')
        parsed = urlsplit(base_url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('TIGER_BASE_URL must be an HTTPS URL without credentials, query or fragment')
        if not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise ValueError('TIGER_TIMEOUT must be between 0 and 60 seconds')
        self._api_key = api_key.strip()
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self._opener = build_opener(_NoRedirect())

    def services(self, station: str) -> list[dict[str, Any]]:
        station = validate_lookup(station, 'A00000')
        request = Request(f'{self.base_url}/services/{station}', headers={
            'Accept': 'application/json', 'Origin': 'https://tiger.worldline.global',
            'x-api-key': self._api_key,
        })
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise TigerError('TIGER response was too large')
            # Upstream echoes must not expose the credential through raw evidence.
            payload = json.loads(raw)
            if self._api_key in json.dumps(payload, ensure_ascii=False):
                raise TigerError('TIGER returned an unsafe response')
        except HTTPError as exc:
            exc.close()
            raise TigerError(f'TIGER upstream returned HTTP {exc.code}') from None
        except (URLError, OSError):
            raise TigerError('Could not reach TIGER upstream') from None
        except (ValueError, UnicodeError):
            raise TigerError('TIGER returned invalid JSON') from None
        if isinstance(payload, dict):
            # TIGER uses HTTP 200 for application-level failures as well.
            # Never relay upstream detail: it may echo credentials or input.
            if payload.get('name') == 'NotFound':
                raise TigerError('TIGER location not found; check the station TIPLOC', 404)
            if 'name' in payload and 'detail' in payload:
                raise TigerError('TIGER returned an application error')
            if payload.get('TIPLOC') not in (None, station):
                raise TigerError('TIGER returned a different station TIPLOC')
            payload = payload.get('Services', payload.get('services'))
        if not isinstance(payload, list) or any(not isinstance(s, dict) for s in payload):
            raise TigerError('TIGER returned an unsupported services payload')
        return payload

    def get_service_details(self, station: str, uid: str, departure_date: str | None = None) -> dict[str, Any]:
        station = validate_lookup(station, uid, departure_date)
        matches = [(s, service_date_evidence(s)) for s in self.services(station) if s.get('UID') == uid]
        # Only an explicit service date can verify a dated match. Never infer it
        # from the current day, a headcode, departure time or UID substring.
        if departure_date:
            matches = [(s, evidence) for s, evidence in matches
                       if evidence['departureDate'] in (None, departure_date)]
        if not matches:
            raise TigerError('No exact TIGER UID match at this station', 404)
        if len(matches) != 1:
            raise TigerError('Ambiguous TIGER UID match at this station', 409)
        service, date_info = matches[0]
        result = normalize_coach_list(service.get('CoachList'))
        result.update({'uid': uid, 'station': station, 'source': 'TIGER',
                       'retrievedAt': datetime.now(timezone.utc).isoformat(),
                       'departureDate': date_info['departureDate'],
                       'dateMatchBasis': date_info['dateMatchBasis'],
                       'dateEvidence': date_info['dateEvidence'],
                       'dateVerified': bool(departure_date and date_info['departureDate'] == departure_date),
                       'rawService': service})
        result['warnings'].extend(date_info['warnings'])
        if not result['dateVerified']:
            result['warnings'].append('Service date is unverified; do not attach this evidence to a dated RTT service.')
        return result


def reconcile_rtt_tiger(rtt: dict[str, Any], tiger: dict[str, Any], station: str,
                        *, requested_station: str | None = None) -> dict[str, Any]:
    """Keep RTT intact; apply coach enrichment only after UID/date/station checks.

    ``rtt`` is get_service_details output. Unknown dates or station membership
    are deliberately not guessed. All TIGER evidence remains inspectable.
    """
    metadata = rtt.get('scheduleMetadata') or {}
    identity = str(metadata.get('uniqueIdentity') or '').removeprefix('gb-nr:')
    expected = f"{tiger['uid']}:{tiger.get('departureDate')}"
    calls = [c for c in rtt.get('calls', []) if station in (
        ((c.get('location') or {}).get('shortCodes') or []) +
        ((c.get('location') or {}).get('longCodes') or []))
        and (requested_station is None or requested_station in (
            ((c.get('location') or {}).get('shortCodes') or []) +
            ((c.get('location') or {}).get('longCodes') or [])))]
    matched = identity == expected and tiger.get('dateVerified') is True and tiger['station'] == station and len(calls) == 1
    conflicts = []
    if matched:
        index = (calls[0].get('locationMetadata') or {}).get('allocationIndex')
        allocations = [a for a in rtt.get('allocationData') or [] if a.get('allocationIndex') == index]
        if index is not None and len(allocations) == 1:
            count = allocations[0].get('passengerVehicles')
            if count is not None and tiger['totalCoaches'] is not None and count != tiger['totalCoaches']:
                conflicts.append({'field': 'totalCoaches', 'rtt': count, 'tiger': tiger['totalCoaches']})
    return {'rtt': rtt, 'tiger': tiger, 'coachEnrichmentApplied': bool(matched and not conflicts and tiger.get('coaches')),
            'conflicts': conflicts,
            'warnings': ([] if matched else ['RTT/TIGER dated identity and station match not verified.']) +
                        (['Coach counts disagree; report both sources.'] if conflicts else []) +
                        (['TIGER coach data is not indicated for this service.'] if not tiger.get('coaches') else []),
            'authority': {'identity': 'RTT', 'times': 'RTT', 'platform': 'RTT',
                          'status': 'RTT', 'route': 'RTT', 'allocation': 'RTT', 'coachFacilities': 'TIGER'}}
