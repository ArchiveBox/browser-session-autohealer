"""Plain adapter for ArchiveBox's vendored OpenCode proxy transport."""

import base64
import logging

from plain.auth.views import AuthView
from plain.http import AsyncStreamingResponse, ForbiddenError403, Response
from plain.http.websocket import is_websocket_upgrade

from .core import opencode_proxy as runtime
from .core.inference import ORIGIN, password

logger = logging.getLogger(__name__)


class OpenCodeProxy(AuthView):
    admin_required = True

    def before_request(self):
        super().before_request()
        upgrade = is_websocket_upgrade(self.request)
        if (upgrade and not self.request.headers.get("Origin")) or not runtime._origin_allowed(
            "POST" if upgrade else self.request.method,
            self.request.host,
            self.request.headers,
        ):
            raise ForbiddenError403("Cross-origin agent requests are blocked.")
        self.proxy_settings = {
            "origin": ORIGIN,
            "timeout": 120,
            # The browser uses its existing app session; the private upstream
            # credential never leaves the server (HTTP, event stream, or socket).
            "authorization": "Basic "
            + base64.b64encode(f"opencode:{password()}".encode()).decode(),
        }

    def get(self):
        try:
            status, headers, body = runtime.proxy(
                self.proxy_settings,
                self.request.method,
                self.url_kwargs.get("path", ""),
                tuple(
                    (key, value)
                    for key, values in self.request.query_params.lists()
                    for value in values
                ),
                self.request.headers,
                self.request.body,
            )
            # A backend credential failure is a service error, never a second
            # browser login dialog. App authentication was already checked.
            if status == 401:
                raise RuntimeError("OpenCode rejected its internal credential")
            headers["X-Frame-Options"] = "SAMEORIGIN"
            headers["Content-Security-Policy"] = "frame-ancestors 'self'"
            response_type = Response if isinstance(body, bytes) else AsyncStreamingResponse
            return response_type(body, status_code=status, headers=headers)
        except Exception:
            logger.exception("OpenCode proxy failed")
            return Response("Agent service unavailable.", status_code=503)

    post = put = patch = delete = get

    async def websocket(self, ws):
        # Adapt Plain's socket messages to the ASGI receive/send contract used
        # by ArchiveBox. Forwarding, cancellation and close logic stay upstream.
        async def receive():
            try:
                payload = await anext(ws)
            except StopAsyncIteration:
                return {"type": "websocket.disconnect"}
            return {
                "type": "websocket.receive",
                "bytes" if isinstance(payload, bytes) else "text": payload,
            }

        async def send(event):
            if event["type"] == "websocket.send":
                await ws.send(event.get("bytes", event.get("text")))
            elif event["type"] == "websocket.close":
                await ws.close(event["code"])

        await runtime.websocket_proxy(
            self.proxy_settings,
            self.url_kwargs.get("path", ""),
            self.request.query_string.encode("ascii"),
            [ws.subprotocol] if ws.subprotocol else [],
            receive,
            send,
        )
