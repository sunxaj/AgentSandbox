#!/usr/bin/env python3
"""Expose the project's existing MCP tools over Streamable HTTP.

The stdio server remains the default for local MCP hosts and MCP Inspector.
This entry point imports the same registered FastMCP instance and starts it
with Streamable HTTP transport, so Bonsai's llama.cpp UI can reach the tools
over the Mac's private network without maintaining a second copy of them.
"""

from __future__ import annotations

import configparser
import hmac
import ipaddress
import json
import os
from pathlib import Path
from typing import Any, Awaitable, Callable

from mcp.server.transport_security import TransportSecuritySettings
import uvicorn

from mcp_server import mcp

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CorsMiddleware:
    """Add CORS headers to every response and short-circuit OPTIONS preflight requests.

    The browser sends a preflight OPTIONS request before any request that carries
    a custom header (e.g. Authorization). Without an explicit 200 response to that
    preflight the browser blocks the actual request before it reaches the server.

    This middleware is the outermost ASGI layer so it intercepts OPTIONS before
    BearerTokenMiddleware or the MCP SDK can reject it. It also validates the
    Origin and Host headers itself, which lets us disable the SDK's built-in
    TransportSecurityMiddleware — that middleware runs inside the SDK's ASGI
    __call__ and would return error responses without CORS headers attached,
    causing the browser to swallow the real error and report only a CORS failure.
    """

    def __init__(self, app: Callable[..., Awaitable[None]],
                 allowed_origins: list[str], allowed_hosts: list[str]) -> None:
        self.app = app
        self._origins = set(allowed_origins)
        self._hosts = set(allowed_hosts)

    def _origin_allowed(self, origin: str) -> bool:
        return origin in self._origins or "*" in self._origins

    def _host_allowed(self, host: str) -> bool:
        if host in self._hosts:
            return True
        # Support wildcard port patterns e.g. "192.168.0.222:*"
        return any(
            h.endswith(":*") and host.startswith(h[:-1])
            for h in self._hosts
        )

    def _cors_headers(self, origin: str) -> list[tuple[bytes, bytes]]:
        """Reflect the requesting origin when allowed; empty string otherwise."""
        allowed = origin if self._origin_allowed(origin) else ""
        return [
            (b"access-control-allow-origin", allowed.encode()),
            (b"access-control-allow-methods", b"GET, POST, DELETE, OPTIONS"),
            (b"access-control-allow-headers", b"Authorization, authorization, Content-Type, content-type, mcp-session-id, last-event-id, accept, Accept"),
            (b"access-control-max-age", b"600"),
        ]

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        origin = headers.get(b"origin", b"").decode("utf-8", errors="replace")
        host = headers.get(b"host", b"").decode("utf-8", errors="replace")
        cors = self._cors_headers(origin)

        # Validate Host header to preserve DNS rebinding protection.
        if self._hosts and not self._host_allowed(host):
            body = json.dumps({"error": "Invalid Host header"}).encode()
            await send({"type": "http.response.start", "status": 421,
                        "headers": cors + [(b"content-type", b"application/json"),
                                           (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return

        # Validate Origin header (absent origin = same-origin request, always allowed).
        if origin and not self._origin_allowed(origin):
            body = json.dumps({"error": "Invalid Origin header"}).encode()
            await send({"type": "http.response.start", "status": 403,
                        "headers": cors + [(b"content-type", b"application/json"),
                                           (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return

        # Respond to preflight immediately; do not forward to the MCP app.
        if scope["method"] == "OPTIONS":
            await send({"type": "http.response.start", "status": 200, "headers": cors})
            await send({"type": "http.response.body", "body": b""})
            return

        # Inject CORS headers into every real response.
        async def send_with_cors(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                message = {**message, "headers": list(message.get("headers", [])) + cors}
            await send(message)

        await self.app(scope, receive, send_with_cors)


class BearerTokenMiddleware:
    """Require a shared bearer token before forwarding an HTTP request to MCP.

    Bonsai's llama.cpp MCP settings support custom request headers, so this
    small ASGI wrapper protects the LAN endpoint without changing the shared
    tool definitions or adding authentication code to each worker.
    OPTIONS preflight requests are passed through without a token check because
    the browser sends them before it can attach the Authorization header.
    """

    def __init__(self, app: Callable[..., Awaitable[None]], token: str) -> None:
        """Remember the wrapped MCP app and the secret supplied at launch time."""
        self.app = app
        self.expected = token.encode("utf-8")

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        """Reject missing or incorrect Authorization headers with HTTP 401."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # Preflight requests carry no Authorization header by design; let them
        # through so CorsMiddleware (outer layer) can respond to them.
        if scope["method"] == "OPTIONS":
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        supplied = headers.get(b"authorization", b"")
        expected_header = b"Bearer " + self.expected
        if not hmac.compare_digest(supplied, expected_header):
            body = json.dumps({"error": "Unauthorized"}).encode("utf-8")
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"content-length", str(len(body)).encode("ascii"))]})
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


def _load_http_settings() -> tuple[str, int, str, list[str], list[str]]:
    """Load bind and browser/host allowlists from the project HTTP config."""
    config_path = PROJECT_ROOT / "mcp" / "config" / "mcp_http.conf"
    parser = configparser.ConfigParser()
    if not parser.read(config_path):
        raise FileNotFoundError(f"MCP HTTP config not found: {config_path}")

    section = parser["http"]
    host = section.get("host", "127.0.0.1").strip()
    port = section.getint("port", 8000)
    endpoint_path = section.get("path", "/mcp").strip()
    if not endpoint_path.startswith("/"):
        endpoint_path = "/" + endpoint_path
    allowed_hosts = [
        value.strip() for value in section.get("allowed_hosts", "").split(",")
        if value.strip()
    ]
    allowed_origins = [
        value.strip() for value in section.get("allowed_origins", "").split(",")
        if value.strip()
    ]

    if not 1 <= port <= 65535:
        raise ValueError("MCP HTTP port must be between 1 and 65535.")
    if not allowed_hosts:
        raise ValueError("Configure at least one Host header in allowed_hosts.")
    return host, port, endpoint_path, allowed_hosts, allowed_origins


def main() -> None:
    """Apply the configured network boundary and serve the shared MCP tools."""
    host, port, endpoint_path, allowed_hosts, allowed_origins = _load_http_settings()

    # Bind the endpoint to a specific interface and use a token for LAN access.
    # SDK Host checks prevent DNS rebinding; the token authenticates callers.
    # Keep the firewall restricted as a second boundary around write-capable tools.
    token = os.getenv("MCP_HTTP_BEARER_TOKEN", "").strip()
    try:
        loopback_only = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback_only = host.lower() == "localhost"
    if not loopback_only and not token:
        raise RuntimeError(
            "MCP_HTTP_BEARER_TOKEN is required when the HTTP server binds to a non-loopback address."
        )

    mcp.settings.host = host
    mcp.settings.port = port
    mcp.settings.streamable_http_path = endpoint_path
    # Disable the SDK's built-in DNS rebinding protection: it runs inside the
    # SDK's ASGI __call__ and returns error responses without CORS headers,
    # causing the browser to see only a CORS failure instead of the real error.
    # CorsMiddleware (outermost layer) replicates the same Host/Origin checks
    # with CORS headers attached to every response including errors.
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    )
    app = mcp.streamable_http_app()
    if token:
        app = BearerTokenMiddleware(app, token)
    # CorsMiddleware is outermost: handles OPTIONS before BearerTokenMiddleware
    # can reject the preflight, and validates Host/Origin itself.
    app = CorsMiddleware(app, allowed_origins, allowed_hosts)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
