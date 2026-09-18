"""Explicit model-facing schema for TIGER evidence."""


def tiger_operation(errors):
    nullable_label = {'type': ['string', 'null']}
    coach = {'type': 'object', 'properties': {
        'coachNumber': {'type': ['integer', 'null']}, 'coachLetter': nullable_label,
        **{name: {'type': ['boolean', 'null']} for name in (
            'leadingPowerCar', 'trailingPowerCar', 'firstClass', 'standardClass',
            'wheelchairs', 'bikeStorage', 'catering')},
    }}
    evidence = {'type': 'object', 'properties': {
        'uid': {'type': 'string'}, 'station': {'type': 'string'},
        'source': {'type': 'string', 'const': 'TIGER'},
        'retrievedAt': {'type': 'string', 'format': 'date-time'},
        'departureDate': nullable_label, 'dateVerified': {'type': 'boolean'},
        'dateMatchBasis': nullable_label,
        'dateEvidence': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'path': {'type': 'string'}, 'value': {'type': 'string'}}}},
        'totalCoaches': {'type': ['integer', 'null']},
        'orientationKnown': {'type': 'boolean'}, 'frontCoach': nullable_label, 'rearCoach': nullable_label,
        **{name: {'type': 'array', 'items': {'type': 'string'}} for name in (
            'firstClassCoaches', 'wheelchairCoaches', 'bikeCoaches', 'cateringCoaches', 'warnings')},
        'coaches': {'type': 'array', 'items': coach},
        'rawCoachList': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': True}},
        'rawService': {'type': 'object', 'additionalProperties': True},
    }, 'required': ['uid', 'station', 'orientationKnown', 'dateVerified', 'coaches']}
    reconciliation = {'type': 'object', 'properties': {
        'rtt': {'type': 'object', 'additionalProperties': True}, 'tiger': evidence,
        'coachEnrichmentApplied': {'type': 'boolean'},
        'authority': {'type': 'object', 'additionalProperties': {'type': 'string'}},
        'conflicts': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': True}},
        'warnings': {'type': 'array', 'items': {'type': 'string'}},
    }, 'required': ['rtt', 'tiger', 'coachEnrichmentApplied', 'conflicts']}
    return {
        'operationId': 'getTigerServiceDetails',
        'summary': 'Get TIGER coach evidence for an exact RTT UID at a station',
        'description': (
            'First establish the dated service with RTT. TIGER requires a TIPLOC and exact UID. '
            'A CRS station requires unique_identity for RTT-based resolution, or an explicit tiploc. '
            'TIGER supplies passenger facilities only; RTT remains authoritative for identity, times, '
            'platform, status, route and allocation. Missing flags mean not indicated. '
            'Do not infer orientation from letters or class. Date evidence uses scheduled origin departure '
            'timestamps in Europe/London; station and forecast timestamps do not establish service dates. '
            'An unverified date prevents dated enrichment. '
            'Supply unique_identity to return RTT and TIGER evidence with reconciliation warnings.'),
        'parameters': [
            {'name': 'station', 'in': 'query', 'required': True,
             'schema': {'type': 'string', 'pattern': '^[A-Za-z0-9]{3,7}$'}},
            {'name': 'tiploc', 'in': 'query', 'required': False,
             'description': 'Explicit TIGER TIPLOC, for example PADTON for RTT CRS PAD. Never send CRS as TIPLOC.',
             'schema': {'type': 'string', 'pattern': '^[A-Za-z0-9]{3,7}$'}},
            {'name': 'uid', 'in': 'query', 'required': True,
             'schema': {'type': 'string', 'pattern': '^[A-Z][0-9]{5}$'}},
            {'name': 'departure_date', 'in': 'query', 'required': False,
             'description': 'RTT service departure date; never inferred from the current date.',
             'schema': {'type': 'string', 'format': 'date'}},
            {'name': 'unique_identity', 'in': 'query', 'required': False,
             'description': 'Exact opaque uniqueIdentity returned by RTT, for reconciliation.',
             'schema': {'type': 'string'}},
        ],
        'responses': {**errors,
            '404': {'description': 'TIGER location not found, or no exact UID/date match'},
            '409': {'description': 'Multiple matching TIGER services; no selection made'},
            '502': {'description': 'TIGER or RTT upstream error'},
            '503': {'description': 'TIGER is not configured'},
            '200': {'description': 'TIGER evidence, optionally reconciled with RTT', 'content': {
                'application/json': {'schema': {'type': 'object', 'required': ['ok', 'result'],
                    'properties': {'ok': {'type': 'boolean'}, 'result': {'oneOf': [evidence, reconciliation]}}}}}},
    }
    }
