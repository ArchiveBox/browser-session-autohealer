"""ArchiveBox OpenCode HTTP/SSE/WebSocket transport, vendored under MIT.

Source: abx-plugins/abx_plugins/plugins/opencode/runtime.py
Revision: ad1847a2b1a47d327b1f9b1fd3c357469dec815c
See opencode_proxy.LICENSE and docs/opencode-embedding.md for local changes.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import requests

_ASSETS = {}
_LOGGER = logging.getLogger(__name__)
_PROXY_PREFIX = "/agents/opencode"
_TEXT_CONTENT_TYPES = ("text/", "application/javascript", "application/x-javascript")
_HOP_BY_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}
_READY = False


def _owned_process_ready():
    return _READY


def _ensure_opencode(settings):
    from .inference import ensure_server

    global _READY
    ensure_server()
    _READY = True
    return True, ""


def _origin_allowed(method: str, expected_host: str, headers) -> bool:
    """Called by the host's authenticated adapter before invoking proxy()."""
    if method in {"GET", "HEAD", "OPTIONS", "TRACE"}:
        return True

    origin = headers.get("Origin")
    if origin:
        return _same_host(origin, expected_host)

    referer = headers.get("Referer")
    if referer:
        return _same_host(referer, expected_host)

    fetch_site = headers.get("Sec-Fetch-Site")
    if fetch_site:
        return fetch_site in {"same-origin", "same-site", "none"}

    return False


def _same_host(value: str, expected_host: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and parsed.netloc == expected_host


def _project_route(workdir: Path, session_id: str = "") -> str:
    encoded = base64.b64encode(str(workdir.resolve()).encode()).decode()
    encoded = encoded.replace("+", "-").replace("/", "_").rstrip("=")
    route = f"{_PROXY_PREFIX}/{encoded}/session"
    return f"{route}/{session_id}" if session_id else route


def _proxy_url(settings: dict, path: str | None) -> str:
    return settings["origin"] + "/" + (path or "").lstrip("/")


def _rewrite_text(body: bytes, origin: str) -> bytes:
    text = body.decode("utf-8", errors="replace")
    text = text.replace(origin, _PROXY_PREFIX)
    # Configure the native router, not browser history or pathname. Minifier
    # identifiers change between builds; the public router prop does not.
    text = re.sub(
        r"(get component\(\)\{return [$\w]+\.router\?\?[$\w]+\},)",
        rf'\1base:"{_PROXY_PREFIX}",',
        text,
    )
    # New-layout titlebar tabs render plain anchors instead of router links.
    # Prefix only their rendered href; navigation still receives native routes.
    text = re.sub(
        r"(get href\(\)\{return )([$\w]+\([$\w]+\.tab\))(\})",
        rf'\1"{_PROXY_PREFIX}"+\2\3',
        text,
    )
    # The native router keeps the mount in useLocation().pathname. OpenCode's
    # draft promotion, tab closing, legacy redirect, SDK scope checks, and layout
    # route classifier (pathname, search) expect app-relative paths. Normalize
    # only those reads, never browser/router state. Otherwise Home stays selected
    # on every mounted session and its button cannot navigate back to Home.
    text = re.sub(
        r'([$\w]+)\.pathname(?===="/new-session"|!=="/"|\.startsWith\("/api/"\)|\.slice\([$\w]+\(\)\.length\+1\)|,\1\.search\))',
        lambda match: (
            f'({match[0]}.replace(/^{_PROXY_PREFIX.replace("/", r"\/")}(?=\\/|$)/,"")||"/")'
        ),
        text,
    )
    # Only the web entrypoint's default server needs the mount prefix. Changing
    # location.origin globally breaks the router's same-origin link interception.
    text = text.replace(
        '?"http://localhost:4096":location.origin',
        f'?"http://localhost:4096":location.origin+"{_PROXY_PREFIX}"',
    )
    # The newer SDK and protocol probe use URL(path, server). A leading slash
    # discards the server's mount path, making the probe misidentify a v1 server
    # as v2. Resolve relative endpoints against a directory base instead.
    text = re.sub(
        r"new URL\(([$\w]+\.path),([$\w]+\.baseUrl)\)",
        r'new URL(\1.replace(/^\//,""),\2.replace(/\/?$/,"/"))',
        text,
    )
    text = re.sub(
        r"new URL\(([$\w]+),([$\w]+\.url)\)",
        r'new URL(\1.replace(/^\//,""),\2.replace(/\/?$/,"/"))',
        text,
    )
    text = text.replace('"/assets/', f'"{_PROXY_PREFIX}/assets/')
    text = text.replace("'/assets/", f"'{_PROXY_PREFIX}/assets/")
    text = re.sub(
        r'("modulepreload",[$\w]+=function\(([$\w]+)\)\{return")/("\+\2\})',
        rf"\1{_PROXY_PREFIX}/\3",
        text,
    )
    # One pass for root-relative HTML, JS, and CSS references. Already-mounted
    # URLs must stay untouched because earlier rewrites can produce them.
    text = re.sub(
        r"""(?P<prefix>\b(?:(?:href|src|action)=["']|(?:fetch|EventSource)\(["']|url\(["']?))/"""
        rf"(?!{_PROXY_PREFIX.lstrip('/')}(?:/|$))",
        rf"\g<prefix>{_PROXY_PREFIX}/",
        text,
    )
    return text.encode("utf-8")


def _response_headers(upstream: requests.Response, settings: dict) -> dict[str, str]:
    headers = {}
    for key, value in upstream.headers.items():
        lower = key.lower()
        if lower in _HOP_BY_HOP_HEADERS or lower in {
            "content-length",
            "content-encoding",
            "x-frame-options",
            "etag",
            "cache-control",
        }:
            continue
        if lower == "location":
            if value.startswith(settings["origin"]):
                value = value.replace(settings["origin"], _PROXY_PREFIX, 1)
            elif value.startswith("/"):
                value = f"{_PROXY_PREFIX}{value}"
        headers[key] = value
    return headers


async def _event_chunks(settings, path, method, params, headers):
    # Stream failures happen after the host returns response headers.
    try:
        if not _owned_process_ready():
            ok, error = await asyncio.to_thread(_ensure_opencode, settings)
            if not ok:
                raise RuntimeError(error)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(settings["timeout"], read=None),
            follow_redirects=False,
        ) as client:
            async with client.stream(
                method,
                _proxy_url(settings, path),
                params=params,
                headers=headers,
            ) as upstream:
                upstream.raise_for_status()
                async for chunk in upstream.aiter_raw():
                    yield chunk
    except Exception:
        _LOGGER.exception("OpenCode event stream failed")
        yield b'event: error\ndata: {"error":"OpenCode unavailable"}\n\n'


async def websocket_proxy(settings, path, query, protocols, receive, send):
    """Forward an authenticated WebSocket; the host consumes the connect event."""
    from websockets.asyncio.client import connect

    tasks = []
    try:
        ok, error = await asyncio.to_thread(_ensure_opencode, settings)
        if not ok:
            raise RuntimeError(error)
        url = _proxy_url(settings, path).replace("http", "ws", 1)
        if query:
            url += "?" + query.decode("ascii")
        async with connect(
            url,
            subprotocols=protocols or None,
            additional_headers={"Authorization": settings["authorization"]},
            proxy=None,
            open_timeout=settings["timeout"],
        ) as upstream:
            await send(
                {"type": "websocket.accept", "subprotocol": upstream.subprotocol},
            )

            async def from_browser():
                while True:
                    event = await receive()
                    if event["type"] == "websocket.disconnect":
                        return
                    if event["type"] == "websocket.receive":
                        payload = event.get("bytes")
                        await upstream.send(
                            payload if payload is not None else event["text"],
                        )

            async def from_upstream():
                async for payload in upstream:
                    kind = "bytes" if isinstance(payload, bytes) else "text"
                    await send({"type": "websocket.send", kind: payload})
                await send({"type": "websocket.close", "code": 1000})

            tasks = [
                asyncio.create_task(from_browser()),
                asyncio.create_task(from_upstream()),
            ]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
    except Exception:
        _LOGGER.exception("OpenCode WebSocket failed")
        await send({"type": "websocket.close", "code": 1011})
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def proxy(settings: dict, method: str, path: str, params, headers, body: bytes):
    """Forward a request after the host authenticates it and checks its origin."""
    forwarded = {
        key: value
        for key, value in headers.items()
        if key.lower()
        in {"accept", "accept-language", "content-type", "range", "user-agent"}
        or key.lower().startswith("x-opencode-")
    }
    forwarded["Authorization"] = settings["authorization"]
    if method == "GET" and (path == "event" or path.endswith("/event")):
        return (
            200,
            {
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
            },
            _event_chunks(settings, path, method, params, forwarded),
        )

    if path == "global/health" or not _owned_process_ready():
        ok, error = _ensure_opencode(settings)
        if not ok:
            raise RuntimeError(error)

    with requests.request(
        method,
        _proxy_url(settings, path),
        params=params,
        data=body if method not in {"GET", "HEAD"} else None,
        headers=forwarded,
        # OAuth callbacks are long polls: OpenCode waits for the human to
        # authorize or cancel, and owns that flow's expiry. Keep connection
        # establishment bounded, but do not turn a pending login into a 503.
        timeout=(settings["timeout"], None)
        if method == "POST" and re.fullmatch(r"provider/[^/]+/oauth/callback", path)
        else settings["timeout"],
        allow_redirects=False,
    ) as upstream:
        content = upstream.content
        response_headers = _response_headers(upstream, settings)
        content_type = upstream.headers.get("Content-Type", "")
        asset = (
            method == "GET"
            and path.startswith("assets/")
            and upstream.status_code == 200
        )
        # Content identity handles new builds without a TTL or restart hook.
        key = (
            (
                settings["origin"],
                content_type,
                hashlib.sha256(content).digest(),
            )
            if asset
            else None
        )
        cached = _ASSETS.get(key) if key is not None else None
        if cached is not None:
            content = cached[0]
        else:
            if content_type.startswith(_TEXT_CONTENT_TYPES):
                content = _rewrite_text(content, settings["origin"])
            if key is not None:
                # Upstream's ETag describes different bytes after rewriting.
                cached = content, f'"{hashlib.sha256(content).hexdigest()}"'
                # Keep only outputs, not a second copy of each multi-MB input.
                if len(_ASSETS) >= 8:
                    _ASSETS.pop(next(iter(_ASSETS), key), None)
                _ASSETS[key] = cached
        response_headers["Cache-Control"] = "no-store"
        if cached is not None:
            # Hashed build assets contain no session data. Cache only privately;
            # API responses and the HTML entrypoint must always remain fresh.
            response_headers["Cache-Control"] = "private, max-age=3600"
            response_headers["ETag"] = cached[1]
            validators = {
                tag.strip().removeprefix("W/")
                for tag in headers.get("If-None-Match", "").split(",")
            }
            if cached[1] in validators or "*" in validators:
                return 304, response_headers, b""
        return upstream.status_code, response_headers, content
