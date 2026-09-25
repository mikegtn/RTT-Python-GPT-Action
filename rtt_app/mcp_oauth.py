"""Private-owner OAuth backed by the existing mikegtn.net admin login.

MCP's SDK handles client authentication, redirect matching and S256 PKCE. This
provider supplies durable, single-use grants and owner approval. Bearer secrets
are stored as hashes; approval is available only to the local PHP login bridge.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.parse import urlencode

from mcp.server.auth.provider import (
    AccessToken, AuthorizationCode, AuthorizationParams, AuthorizeError,
    RefreshToken, RegistrationError, TokenError,
)
from mcp.server.auth.routes import create_auth_routes
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl
from starlette.responses import JSONResponse
from starlette.routing import Route

ISSUER = "https://rail.mikegtn.net"
RESOURCE = ISSUER + "/mcp"
SCOPE = "rail:access"
OWNER_PAGE = "https://mikegtn.net/admin/rtt-oauth.php"
OWNER_URI_RE = re.compile(r"https://chatgpt\.com/connector/oauth/[A-Za-z0-9_-]{1,128}")


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def allowed_redirect(value):
    return value == "https://chatgpt.com/connector_platform_oauth_redirect" or bool(OWNER_URI_RE.fullmatch(value))


class FamilyAccessToken(AccessToken):
    family: str


class FamilyRefreshToken(RefreshToken):
    family: str


class OwnerOAuth:
    def __init__(self, database, bridge_key):
        if len(bridge_key) < 32:
            raise ValueError("A separate owner-bridge key is required")
        self.database = str(database)
        self.bridge_key = bridge_key
        self.rate_windows = {}
        Path(database).parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS records (kind TEXT, key TEXT, data TEXT NOT NULL, expires REAL NOT NULL, PRIMARY KEY(kind,key))")
        Path(database).chmod(0o600)

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.database, timeout=10)
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def put(db, kind, key, data, expiry):
        db.execute("INSERT OR REPLACE INTO records VALUES (?,?,?,?)", (kind, key, json.dumps(data), expiry))

    @staticmethod
    def get(db, kind, key):
        row = db.execute("SELECT data FROM records WHERE kind=? AND key=? AND expires>?", (kind, key, time.time())).fetchone()
        return json.loads(row[0]) if row else None

    async def get_client(self, client_id):
        with self.transaction() as db:
            data = self.get(db, "client", client_id)
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def register_client(self, client_info):
        if not client_info.redirect_uris or not all(allowed_redirect(str(u)) for u in client_info.redirect_uris):
            raise RegistrationError("invalid_redirect_uri", "Only ChatGPT OAuth callbacks are supported by this private integration")
        if client_info.token_endpoint_auth_method not in {"none", "client_secret_post", "client_secret_basic"}:
            raise RegistrationError("invalid_client_metadata", "Unsupported client authentication")
        if set((client_info.scope or SCOPE).split()) != {SCOPE}:
            raise RegistrationError("invalid_client_metadata", "Only rail:access is supported")
        with self.transaction() as db:
            if db.execute("SELECT count(*) FROM records WHERE kind='client'").fetchone()[0] >= 500:
                raise RegistrationError("invalid_client_metadata", "Registration capacity reached")
            self.put(db, "client", client_info.client_id, client_info.model_dump(mode="json"), time.time() + 10 * 365 * 86400)

    async def authorize(self, client, params: AuthorizationParams):
        if params.resource != RESOURCE:
            raise AuthorizeError("invalid_request", "The resource must be the exact RTT MCP URL")
        if set(params.scopes or [SCOPE]) != {SCOPE}:
            raise AuthorizeError("invalid_scope", "Only rail:access is supported")
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", params.code_challenge):
            raise AuthorizeError("invalid_request", "A valid S256 PKCE challenge is required")
        if not allowed_redirect(str(params.redirect_uri)):
            raise AuthorizeError("invalid_request", "Unsupported callback")
        flow = secrets.token_urlsafe(32)
        with self.transaction() as db:
            db.execute("DELETE FROM records WHERE expires<? AND kind!='client'", (time.time(),))
            if db.execute("SELECT count(*) FROM records WHERE kind='pending'").fetchone()[0] >= 100:
                raise AuthorizeError("temporarily_unavailable", "Too many pending requests")
            self.put(db, "pending", digest(flow), {"client_id": client.client_id,
                "client_name": (client.client_name or "ChatGPT")[:120],
                "params": params.model_dump(mode="json")}, time.time() + 600)
        return OWNER_PAGE + "?request=" + flow

    def pending(self, flow):
        with self.transaction() as db:
            data = self.get(db, "pending", digest(flow))
        if not data:
            raise ValueError("This connection request has expired or was already used")
        return {"clientName": data["client_name"], "scope": SCOPE,
                "redirectUri": data["params"]["redirect_uri"], "resource": RESOURCE}

    def approve(self, flow, allowed):
        with self.transaction() as db:
            data = self.get(db, "pending", digest(flow))
            if not data:
                raise ValueError("This connection request has expired or was already used")
            db.execute("DELETE FROM records WHERE kind='pending' AND key=?", (digest(flow),))
            params = data["params"]
            reply = {"state": params.get("state"), "iss": ISSUER}
            if allowed:
                code = secrets.token_urlsafe(32)
                payload = {"code": "", "client_id": data["client_id"], "scopes": [SCOPE],
                           "expires_at": time.time() + 120, "code_challenge": params["code_challenge"],
                           "redirect_uri": params["redirect_uri"],
                           "redirect_uri_provided_explicitly": params["redirect_uri_provided_explicitly"],
                           "resource": RESOURCE, "subject": "site-owner"}
                self.put(db, "code", digest(code), payload, payload["expires_at"])
                reply["code"] = code
            else:
                reply["error"] = "access_denied"
            return params["redirect_uri"] + "?" + urlencode({k: v for k, v in reply.items() if v is not None})

    async def load_authorization_code(self, client, authorization_code):
        with self.transaction() as db:
            data = self.get(db, "code", digest(authorization_code))
        if not data or data["client_id"] != client.client_id:
            return None
        return AuthorizationCode.model_validate({**data, "code": authorization_code})

    def issue(self, db, client_id, family):
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        now = int(time.time())
        common = {"token": "", "client_id": client_id, "scopes": [SCOPE], "resource": RESOURCE,
                  "subject": "site-owner", "family": family}
        self.put(db, "access", digest(access), {**common, "expires_at": now + 3600}, now + 3600)
        self.put(db, "refresh", digest(refresh), {**common, "expires_at": now + 30 * 86400, "used": False}, now + 30 * 86400)
        return OAuthToken(access_token=access, token_type="Bearer", expires_in=3600,
                          refresh_token=refresh, scope=SCOPE)

    async def exchange_authorization_code(self, client, authorization_code):
        with self.transaction() as db:
            data = self.get(db, "code", digest(authorization_code.code))
            if not data or data["client_id"] != client.client_id:
                raise TokenError("invalid_grant", "Code expired or already used")
            db.execute("DELETE FROM records WHERE kind='code' AND key=?", (digest(authorization_code.code),))
            return self.issue(db, client.client_id, secrets.token_hex(16))

    @staticmethod
    def revoke_family(db, family):
        db.execute("DELETE FROM records WHERE kind IN ('access','refresh') AND json_extract(data,'$.family')=?", (family,))

    async def load_refresh_token(self, client, refresh_token):
        with self.transaction() as db:
            data = self.get(db, "refresh", digest(refresh_token))
            if not data or data["client_id"] != client.client_id:
                return None
            if data["used"]:
                self.revoke_family(db, data["family"])
                return None
        return FamilyRefreshToken.model_validate({**data, "token": refresh_token})

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        # Return errors after the transaction so replay revocation commits.
        result = None
        with self.transaction() as db:
            data = self.get(db, "refresh", digest(refresh_token.token))
            if data and data["client_id"] == client.client_id:
                if data["used"]:
                    self.revoke_family(db, data["family"])
                elif set(scopes) == {SCOPE}:
                    data["used"] = True
                    self.put(db, "refresh", digest(refresh_token.token), data, data["expires_at"])
                    db.execute("DELETE FROM records WHERE kind='access' AND json_extract(data,'$.family')=?", (data["family"],))
                    result = self.issue(db, client.client_id, data["family"])
        if result is None:
            raise TokenError("invalid_grant", "Refresh token expired, used, or invalid scope")
        return result

    async def load_access_token(self, token):
        with self.transaction() as db:
            data = self.get(db, "access", digest(token))
        if not data or data["resource"] != RESOURCE or set(data["scopes"]) != {SCOPE}:
            return None
        return FamilyAccessToken.model_validate({**data, "token": token})

    async def revoke_token(self, token):
        with self.transaction() as db:
            self.revoke_family(db, token.family)

    def routes(self):
        routes = create_auth_routes(self, AnyHttpUrl(ISSUER),
            client_registration_options=ClientRegistrationOptions(enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]),
            revocation_options=RevocationOptions(enabled=True))
        # SDK protocol handlers remain responsible for PKCE/client validation.
        # Add RFC 8707 validation before token exchange (also on refresh).
        for route in routes:
            if route.path in {"/token", "/revoke"}:
                inner = route.app
                async def token_guard(scope, receive, send, inner=inner, path=route.path):
                    from starlette.requests import Request
                    request = Request(scope, receive)
                    body = await request.body()
                    if len(body) > 16384:
                        await JSONResponse({"error": "invalid_request"}, status_code=413)(scope, receive, send)
                        return
                    from urllib.parse import parse_qs
                    form = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
                    if path == '/token' and form.get("resource") != [RESOURCE]:
                        await JSONResponse({"error": "invalid_target"}, status_code=400)(scope, receive, send)
                        return
                    # SDK 1.30's revocation model requires this optional field
                    # even for public clients. Authentication is still delegated
                    # to its ClientAuthenticator; an empty secret cannot satisfy
                    # a confidential client's registered secret.
                    if path == '/revoke' and 'client_secret' not in form:
                        body += b'&client_secret='
                    sent = False
                    async def replay():
                        nonlocal sent
                        if not sent:
                            sent = True
                            return {"type": "http.request", "body": body, "more_body": False}
                        return await receive()
                    await inner(scope, replay, send)
                route.app = token_guard

        async def resource_metadata(request):
            return JSONResponse({"resource": RESOURCE, "authorization_servers": [ISSUER],
                                 "scopes_supported": [SCOPE], "bearer_methods_supported": ["header"],
                                 "resource_name": "TrainBrain"})

        async def metadata(request):
            return JSONResponse({"issuer": ISSUER, "authorization_endpoint": ISSUER + "/authorize",
                "token_endpoint": ISSUER + "/token", "registration_endpoint": ISSUER + "/register",
                "revocation_endpoint": ISSUER + "/revoke", "scopes_supported": [SCOPE],
                "response_types_supported": ["code"], "grant_types_supported": ["authorization_code", "refresh_token"],
                "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
                "code_challenge_methods_supported": ["S256"]})

        async def owner(request):
            if request.client.host not in {"127.0.0.1", "::1"} or not secrets.compare_digest(
                request.headers.get("x-rtt-owner-key", "").encode(), self.bridge_key.encode()):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            try:
                data = await request.json()
                flow = data.get("request", "")
                if not isinstance(flow, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", flow):
                    raise ValueError("Invalid connection request")
                if request.url.path == "/owner/lookup":
                    return JSONResponse(self.pending(flow))
                if not isinstance(data.get("allow"), bool):
                    raise ValueError("An explicit decision is required")
                return JSONResponse({"redirect": self.approve(flow, data["allow"])})
            except (ValueError, TypeError):
                return JSONResponse({"error": "Invalid, expired or already used request"}, status_code=400)

        routes = [r for r in routes if r.path != "/.well-known/oauth-authorization-server"]
        routes += [Route("/.well-known/oauth-authorization-server", metadata),
                   Route("/.well-known/oauth-protected-resource", resource_metadata),
                   Route("/.well-known/oauth-protected-resource/mcp", resource_metadata),
                   Route("/owner/lookup", owner, methods=["POST"]),
                   Route("/owner/approve", owner, methods=["POST"])]
        # Bound public auth traffic and request bodies independently of MCP.
        for route in routes:
            inner = route.app
            async def bounded(scope, receive, send, inner=inner):
                now = int(time.time() // 60)
                # A global ceiling is deliberately conservative for a private owner.
                bucket = (scope['path'], now)
                self.rate_windows = {k: v for k, v in self.rate_windows.items() if k[1] == now}
                self.rate_windows[bucket] = self.rate_windows.get(bucket, 0) + 1
                if self.rate_windows[bucket] > (20 if scope['path'] == '/register' else 120):
                    await JSONResponse({'error': 'rate_limit_exceeded'}, status_code=429,
                                       headers={'Retry-After': '60'})(scope, receive, send)
                    return
                body = bytearray()
                while True:
                    message = await receive()
                    if message['type'] == 'http.disconnect':
                        return
                    body.extend(message.get('body', b''))
                    if len(body) > 16384:
                        await JSONResponse({'error': 'invalid_request'}, status_code=413)(scope, receive, send)
                        return
                    if not message.get('more_body', False):
                        break
                sent = False
                async def replay():
                    nonlocal sent
                    if not sent:
                        sent = True
                        return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
                    return await receive()
                async def no_cache(message):
                    if message['type'] == 'http.response.start':
                        message.setdefault('headers', []).extend([(b'cache-control', b'no-store'),
                                                                   (b'referrer-policy', b'no-referrer')])
                    await send(message)
                await inner(scope, replay, no_cache)
            route.app = bounded
        return routes
