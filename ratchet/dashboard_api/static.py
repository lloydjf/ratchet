"""Serve the built SPA (``settings.ratchet_dashboard_dist``) with history-API fallback.

- ``/assets/*`` (content-hashed by Vite): ``Cache-Control: public, max-age=31536000, immutable``.
- ``index.html`` (also served for any unknown non-API path): ``no-cache``.
- Other top-level files (favicon): one hour.
- Paths under ``/api`` never fall back: they get the JSON 404 envelope.
- If the dashboard is not built, every page path returns a small HTML page saying so.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response

from ratchet.config.settings import Settings
from ratchet.errors import NotFoundError

__all__ = ["install_static", "serve_path", "IMMUTABLE", "NO_CACHE", "NOT_BUILT_HTML"]

IMMUTABLE = "public, max-age=31536000, immutable"
NO_CACHE = "no-cache"
_SHORT = "public, max-age=3600"

NOT_BUILT_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Ratchet</title>
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family: system-ui, sans-serif; background: #0b0d10; color: #e6e8eb;
padding: 2rem; line-height: 1.5">
<h1>Ratchet is running</h1>
<p>The dashboard is not built yet. Build it, then reload this page:</p>
<pre style="background:#161a1f;padding:1rem">
cd dashboard &amp;&amp; npm ci &amp;&amp; npm run build</pre>
<p>The API is up at <a style="color:#7cc4ff" href="/api/v1/meta">/api/v1/meta</a>.</p>
</body></html>
"""

_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]


def _is_api(path: str) -> bool:
    return path == "api" or path.startswith("api/")


def _resolve(dist: Path, rel: str) -> Path | None:
    root = dist.resolve()
    candidate = (root / rel).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


def _index(dist: Path) -> Response:
    index = dist / "index.html"
    if not index.is_file():
        return HTMLResponse(NOT_BUILT_HTML, headers={"Cache-Control": NO_CACHE})
    return FileResponse(index, media_type="text/html", headers={"Cache-Control": NO_CACHE})


def serve_path(dist: Path, full_path: str) -> Response:
    rel = full_path.lstrip("/")
    if rel in {"", "index.html"}:
        return _index(dist)
    found = _resolve(dist, rel) if dist.is_dir() else None
    if found is not None:
        cache = IMMUTABLE if rel.startswith("assets/") else _SHORT
        return FileResponse(found, headers={"Cache-Control": cache})
    if rel.startswith("assets/"):
        return PlainTextResponse("Not found", status_code=404, headers={"Cache-Control": NO_CACHE})
    return _index(dist)  # history-API fallback: the SPA routes it


def install_static(app: FastAPI, settings: Settings) -> None:
    """Register last: a catch-all for every method, so unknown API paths 404 as JSON."""

    async def spa(request: Request, full_path: str) -> Response:
        if _is_api(full_path.lstrip("/")):
            raise NotFoundError("No such route.")
        if request.method not in {"GET", "HEAD"}:
            raise NotFoundError("No such route.")
        return await asyncio.to_thread(serve_path, settings.ratchet_dashboard_dist, full_path)

    app.add_api_route("/{full_path:path}", spa, methods=_METHODS, include_in_schema=False)
