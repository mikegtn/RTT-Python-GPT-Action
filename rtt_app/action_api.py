"""HTTPS-ready, dependency-free HTTP API for a ChatGPT GPT Action."""

from __future__ import annotations

import argparse
import json
import os
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit

from .railway_service import (RailwayService, RailResponse as ActionResponse, OPERATIONS, MAX_RESPONSE_BYTES,
                              MAP_ID_RE, _one, _integer, _boolean, _render_route_map, add_evidence)
from .cli import load_dotenv
from .client import RTTClient
from .movebook_route import MovebookRouteEngine
from .rail_assistant import RTTRailTools
from .tiger import TigerClient
from .tiger_icons import ICONS


from .rail_schema import build_openapi_schema


class ActionApplication(RailwayService):
    """Compatibility HTTP authentication and public routes over the shared service."""
    def __init__(self, tools, *, api_key, **kwargs):
        if not api_key.strip():
            raise ValueError("ACTION_API_KEY is required")
        super().__init__(tools, **kwargs)
        self.api_key = api_key.strip()

    def _authorized(self, headers: Mapping[str, str]) -> bool:
        authorization = headers.get("Authorization", "")
        supplied = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        supplied = supplied or headers.get("X-Action-Key", "").strip()
        return bool(supplied) and secrets.compare_digest(supplied, self.api_key)

    def dispatch(
        self, method: str, path: str, params: Mapping[str, list[str]], headers: Mapping[str, str]
    ) -> ActionResponse:
        return add_evidence(path, self._dispatch(method, path, params, headers))


    def _dispatch(
        self, method: str, path: str, params: Mapping[str, list[str]], headers: Mapping[str, str]
    ) -> ActionResponse:
        if method != "GET":
            return ActionResponse(405, {"ok": False, "error": "Method not allowed"})
        if path == "/health":
            return ActionResponse(200, {"ok": True, "service": "rtt-gpt-action"})
        if path == "/openapi.json":
            return ActionResponse(200, build_openapi_schema(self.base_url))
        if path.startswith("/icons/coach-") and path.endswith(".svg"):
            name = path.removeprefix("/icons/coach-").removesuffix(".svg")
            if name in ICONS:
                return ActionResponse(200, ICONS[name], "image/svg+xml; charset=utf-8")
            return ActionResponse(404, {"ok": False, "error": "Icon not found"})
        if path == "/privacy":
            body = (
                "<!doctype html><title>RTT Rail Action privacy</title>"
                "<h1>Privacy</h1><p>This private action forwards rail queries to Realtime Trains. "
                "It does not require or store an OpenAI API key. Server access logs may contain "
                "request time, IP address and query parameters according to the hosting provider.</p>"
                "<p>Generated railway maps are stored under unguessable links and contain the "
                "requested origin, destination, route geometry and selected via points. The map "
                "files do not contain credentials or client identifiers.</p>"
            )
            return ActionResponse(200, body, "text/html; charset=utf-8")
        if path.startswith("/maps/"):
            return self._route_map(path.removeprefix("/maps/"))
        if not path.startswith("/v1/"):
            return ActionResponse(404, {"ok": False, "error": "Not found"})
        if not self._authorized(headers):
            return ActionResponse(401, {"ok": False, "error": "Unauthorized"})
        return self._execute(path, params)


def make_handler(application: ActionApplication) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "RTTAction/1.0"

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            parsed = urlsplit(self.path)
            response = application.dispatch(
                "GET", parsed.path.rstrip("/") or "/", parse_qs(parsed.query), self.headers
            )
            application.record_request(parsed.path.rstrip("/") or "/", response.status)
            evidence = response.body.get("requestEvidence") if isinstance(response.body, dict) else None
            if evidence:
                print(json.dumps({**evidence, "status": response.status}), flush=True)
            if isinstance(response.body, bytes):
                payload = response.body
            elif isinstance(response.body, str):
                payload = response.body.encode("utf-8")
            else:
                payload = json.dumps(response.body, ensure_ascii=False).encode("utf-8")
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "public, max-age=604800, immutable" if response.content_type == "image/png" else "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:
            # The structured completion event above excludes queries, credentials
            # and client addresses, and is flushed for reliable action audits.
            pass

    return Handler


def create_application() -> ActionApplication:
    load_dotenv()
    token = os.environ.get("RTT_TOKEN", "").strip()
    action_key = os.environ.get("ACTION_API_KEY", "").strip()
    if not token:
        raise ValueError("RTT_TOKEN is required in .env or the environment")
    if not action_key:
        raise ValueError("ACTION_API_KEY is required in .env or the environment")
    client = RTTClient(
        token,
        base_url=os.environ.get("RTT_BASE_URL", "https://data.rtt.io"),
        api_version=os.environ.get("RTT_API_VERSION"),
        token_type=os.environ.get("RTT_TOKEN_TYPE", "auto"),
    )
    return ActionApplication(
        RTTRailTools(client),
        api_key=action_key,
        base_url=os.environ.get("ACTION_BASE_URL", "http://127.0.0.1:8765"),
        usage_file=os.environ.get("ACTION_USAGE_FILE"),
        route_engine=(
            MovebookRouteEngine(
                os.environ["MOVEBOOK_ROUTE_SCRIPT"],
                python=os.environ.get("MOVEBOOK_PYTHON", "/usr/bin/python3"),
            )
            if os.environ.get("MOVEBOOK_ROUTE_SCRIPT")
            else None
        ),
        map_dir=os.environ.get("ACTION_MAP_DIR"),
        tiger_client=(
            TigerClient(
                os.environ["TIGER_API_KEY"],
                base_url=os.environ.get("TIGER_BASE_URL", "https://tiger-api-portal.worldline.global"),
                timeout=float(os.environ.get("TIGER_TIMEOUT", "15")),
            ) if os.environ.get("TIGER_API_KEY", "").strip() else None
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the RTT ChatGPT Action API")
    parser.add_argument("--host", default=os.environ.get("ACTION_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("ACTION_PORT", os.environ.get("PORT", "8765")))
    )
    args = parser.parse_args()
    application = create_application()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(application))
    print(f"RTT Action listening on http://{args.host}:{args.port}")
    print(f"OpenAPI schema: {application.base_url}/openapi.json")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
