"""Operation contracts shared by the HTTP and MCP transports."""
from typing import Any
from .tiger_schema import tiger_operation

def build_openapi_schema(base_url: str) -> dict[str, Any]:
    """Return the schema to paste/import in the GPT Action editor."""
    server = base_url.rstrip("/")
    error = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}, "error": {"type": "string"}},
        "required": ["ok", "error"],
    }
    json_response = {
        "description": "Successful RTT response",
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "ok": {
                            "type": "boolean",
                            "description": "Whether the request completed successfully.",
                        }
                    },
                    "additionalProperties": True,
                }
            }
        },
    }
    usage_response = {
        "description": "Aggregate Action API usage statistics",
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "ok": {"type": "boolean"},
                        "result": {
                            "type": "object",
                            "properties": {
                                "trackingSince": {
                                    "type": "string",
                                    "format": "date-time",
                                    "description": "UTC time from which the counters are available.",
                                },
                                "lastRequestAt": {
                                    "type": ["string", "null"],
                                    "format": "date-time",
                                    "description": "UTC time of the most recently counted request.",
                                },
                                "totalRequests": {"type": "integer", "minimum": 0},
                                "requestsByEndpoint": {
                                    "type": "object",
                                    "additionalProperties": {"type": "integer", "minimum": 0},
                                },
                                "responsesByStatus": {
                                    "type": "object",
                                    "additionalProperties": {"type": "integer", "minimum": 0},
                                },
                            },
                            "required": [
                                "trackingSince",
                                "lastRequestAt",
                                "totalRequests",
                                "requestsByEndpoint",
                                "responsesByStatus",
                            ],
                        },
                    },
                    "required": ["ok", "result"],
                }
            }
        },
    }
    error_responses = {
        "400": {
            "description": "Invalid query",
            "content": {"application/json": {"schema": error}},
        },
        "401": {
            "description": "Missing or incorrect action API key",
            "content": {"application/json": {"schema": error}},
        },
        "502": {
            "description": "RTT upstream API error",
            "content": {"application/json": {"schema": error}},
        },
        "503": {
            "description": "Route engine unavailable",
            "content": {"application/json": {"schema": error}},
        },
    }
    station = {
        "name": "station",
        "in": "query",
        "required": True,
        "description": "UK station name or CRS code, such as Bristol Temple Meads or BRI.",
        "schema": {"type": "string"},
    }
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Realtime Trains Rail Service Action",
            "description": (
                "Read live and scheduled UK rail services, allocations and Know Your Train "
                "coach data from the Realtime Trains API. Data can be absent or change."
            ),
            "version": "1.3.0",
        },
        "servers": [{"url": server}],
        "security": [{"bearerAuth": []}],
        "components": {"schemas": {}, "securitySchemes": {
            "bearerAuth": {"type": "http", "scheme": "bearer"},
        }},
        "paths": {
            "/v1/departures": {
                "get": {
                    "operationId": "getNextDepartures",
                    "summary": "Get the next passenger departures",
                    "description": "Returns times, destination, platform, allocation and live status.",
                    "parameters": [
                        station,
                        {
                            "name": "count",
                            "in": "query",
                            "description": "Number of departures to return.",
                            "schema": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                        },
                        {
                            "name": "minutes",
                            "in": "query",
                            "description": "How far ahead to search in minutes.",
                            "schema": {"type": "integer", "minimum": 1, "maximum": 1439, "default": 180},
                        },
                    ],
                    "responses": {"200": json_response, **error_responses},
                }
            },
            "/v1/services": {
                "get": {
                    "operationId": "searchStationServices",
                    "summary": "Search arrivals or departures at a station",
                    "description": (
                        "Find a particular working or candidates for a forming service. Returns "
                        "uniqueIdentity values that can be passed to getServiceDetails."
                    ),
                    "parameters": [
                        station,
                        {
                            "name": "time_from",
                            "in": "query",
                            "description": "ISO-8601 start datetime. Omit to start now.",
                            "schema": {"type": "string", "format": "date-time"},
                        },
                        {
                            "name": "time_to",
                            "in": "query",
                            "description": "ISO-8601 end datetime. Do not combine with minutes.",
                            "schema": {"type": "string", "format": "date-time"},
                        },
                        {
                            "name": "minutes",
                            "in": "query",
                            "description": "Window length when time_to is omitted.",
                            "schema": {"type": "integer", "minimum": 1, "maximum": 1439, "default": 180},
                        },
                        {
                            "name": "filter_from",
                            "in": "query",
                            "description": "Only services previously calling at this station name or CRS code.",
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "filter_to",
                            "in": "query",
                            "description": "Only services subsequently calling at this station name or CRS code.",
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "movement",
                            "in": "query",
                            "description": "Whether to return arrivals, departures or both.",
                            "schema": {"type": "string", "enum": ["all", "arrivals", "departures"], "default": "all"},
                        },
                        {
                            "name": "count",
                            "in": "query",
                            "description": "Maximum services to return.",
                            "schema": {"type": "integer", "minimum": 1, "maximum": 40, "default": 20},
                        },
                    ],
                    "responses": {"200": json_response, **error_responses},
                }
            },
            "/v1/tiger/service": {"get": tiger_operation(error_responses)},
            "/v1/service": {
                "get": {
                    "operationId": "getServiceDetails",
                    "summary": "Get detailed data for one train service",
                    "description": (
                        "Returns calls, allocation identities, associations and Know Your Train "
                        "coach or formation data when RTT supplies it."
                    ),
                    "parameters": [
                        {
                            "name": "unique_identity",
                            "in": "query",
                            "required": True,
                            "description": "The exact uniqueIdentity from searchStationServices.",
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": json_response, **error_responses},
                }
            },
            "/v1/info": {
                "get": {
                    "operationId": "getRttApiInfo",
                    "summary": "Get RTT API version and entitlements",
                    "description": "Returns the active RTT API metadata for diagnostics.",
                    "responses": {"200": json_response, **error_responses},
                }
            },
            "/v1/usage": {
                "get": {
                    "operationId": "getApiUsage",
                    "summary": "Get hosted Action API usage",
                    "description": (
                        "Returns aggregate request counts by endpoint and HTTP status. "
                        "No credentials, query values or client identifiers are returned."
                    ),
                    "responses": {"200": usage_response, **error_responses},
                }
            },
            "/v1/route": {
                "get": {
                    "operationId": "suggestRailRoute",
                    "summary": "Suggest a topology-backed railway route and map",
                    "description": (
                        "Uses the Movebook railway routing engine to resolve an origin and "
                        "destination, calculate railway mileage and geometry, and return a map "
                        "link plus valid alternative via points. This is an infrastructure route, "
                        "not a timetable, ticket or guaranteed passenger itinerary."
                    ),
                    "parameters": [
                        {
                            "name": "include_snapshot",
                            "in": "query",
                            "description": "Set true only when the user requests a static map image. Returns mapImageUrl for Markdown embedding, fitted to the entire route.",
                            "schema": {"type": "boolean", "default": False},
                        },
                        {
                            "name": "origin",
                            "in": "query",
                            "required": True,
                            "description": "Origin station name or CRS code.",
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "destination",
                            "in": "query",
                            "required": True,
                            "description": "Destination station name or CRS code.",
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "via",
                            "in": "query",
                            "description": (
                                "Optional comma-separated TIPLOC codes selected from candidates "
                                "returned by an earlier route call, in travel order."
                            ),
                            "schema": {"type": "string"},
                        },
                    ],
                    "responses": {"200": json_response, **error_responses},
                }
            },
            "/v1/journey-route": {
                "get": {
                    "operationId": "getJourneyRoute",
                    "summary": "Map a dated itinerary from verified RTT services",
                    "description": "Use for maps of actual trains or connecting journeys. Fetches each exact RTT service and follows its calls and passing points in order. Returns mapUrl, leg evidence and warnings. Geometry between schedule points is inferred; minimum connection times are not verified.",
                    "parameters": [
                        {"name": "legs", "in": "query", "required": True,
                         "description": 'JSON array of 1-6 ordered legs, each with unique_identity (exact RTT uniqueIdentity), origin and destination (station name or CRS). Example: [{"unique_identity":"gb-nr:G01162:2026-09-19","origin":"NCL","destination":"PLY"}]. Never invent identities.',
                         "schema": {"type": "string", "maxLength": 8000}},
                        {"name": "include_snapshot", "in": "query",
                         "description": "Default false. True only for an explicit request for a static snapshot or embedded image; a route map request alone means an interactive link.",
                         "schema": {"type": "boolean", "default": False}},
                    ],
                    "responses": {"200": json_response, **error_responses},
                }
            },
            "/v1/map-snapshot": {
                "get": {
                    "operationId": "getRailMapSnapshot",
                    "summary": "Create a static image of an existing route map",
                    "description": "On user request, render a saved route as a whole-route PNG. Use the id from the returned mapUrl. Embed returned mapImageUrl as a Markdown image and keep mapUrl as an interactive link.",
                    "parameters": [{"name": "map_id", "in": "query", "required": True,
                                    "description": "The 24-character id at the end of a returned mapUrl.",
                                    "schema": {"type": "string", "pattern": "^[a-f0-9]{24}$"}}],
                    "responses": {"200": json_response, **error_responses},
                }
            },
        },
    }


