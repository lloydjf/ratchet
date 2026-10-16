"""SPA serving: history fallback, cache headers, API paths never fall back, unbuilt page."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_api_fakes import ApiEnv, api_reset, make_env  # noqa: E402

__all__ = ["api_reset"]
INDEX = "<!doctype html><title>Ratchet</title><div id=root></div>"


def _built(tmp_path: Path) -> ApiEnv:
    env = make_env(tmp_path)
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX)
    (dist / "assets" / "App-abc123.js").write_text("console.log(1)")
    (dist / "favicon.svg").write_text("<svg/>")
    (tmp_path / "secret.txt").write_text("nope")
    return env


async def test_index_and_history_fallback_are_no_cache(tmp_path: Path) -> None:
    env = _built(tmp_path)
    async with env.client() as client:
        root = await client.get("/")
        deep = await client.get("/verdicts/verdict_abc/pairs/g/r")
    for res in (root, deep):
        assert res.status_code == 200 and res.text == INDEX
        assert res.headers["cache-control"] == "no-cache"
        assert res.headers["content-type"].startswith("text/html")


async def test_hashed_assets_are_immutable_and_missing_assets_404(tmp_path: Path) -> None:
    env = _built(tmp_path)
    async with env.client() as client:
        asset = await client.get("/assets/App-abc123.js")
        missing = await client.get("/assets/App-gone.js")
        icon = await client.get("/favicon.svg")
    assert asset.status_code == 200
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert missing.status_code == 404
    assert icon.headers["cache-control"] == "public, max-age=3600"


async def test_paths_cannot_escape_the_dist_dir(tmp_path: Path) -> None:
    env = _built(tmp_path)
    async with env.client() as client:
        res = await client.get("/..%2Fsecret.txt")
    assert "nope" not in res.text


async def test_api_paths_never_fall_back_to_the_spa(tmp_path: Path) -> None:
    env = _built(tmp_path)
    async with env.client() as client:
        res = await client.get("/api/v1/unknown")
    assert res.status_code == 404 and res.json()["error"]["code"] == "not_found"


async def test_unbuilt_dashboard_serves_a_hint_page(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    async with env.client() as client:
        res = await client.get("/")
        deep = await client.get("/timeline")
    assert res.status_code == 200 and "npm run build" in res.text
    assert deep.status_code == 200 and "not built" in deep.text
