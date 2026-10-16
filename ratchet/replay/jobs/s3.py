"""A minimal S3 client (AWS Signature V4 over httpx) for Nebius Object Storage.

Only what batch I/O needs: PUT object, GET object, ListObjectsV2, and directory
upload/download under a prefix. Written instead of adding ``boto3`` (dependencies are kept
minimal): ~150 lines versus a large dependency tree in the worker image and lockfile, and the
same retry/timeout policy as the Jobs REST client. Path-style addressing
(``<endpoint>/<bucket>/<key>``); the endpoint is ``https://storage.<region>.nebius.cloud``
and the signing region is the region id (docs/research/nebius-tavily.md §B5).

Still unverified: Nebius accepts path-style requests and ``UNSIGNED``-free SigV4 with
``x-amz-content-sha256`` set to the payload hash.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree

import httpx

from ratchet import clock
from ratchet.errors import UpstreamError

__all__ = ["S3Client", "sigv4_headers", "send_with_retries", "BACKOFF_S"]

BACKOFF_S = (2.0, 4.0, 8.0)  # Nebius calls: 3 retries
_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
_EMPTY_SHA = hashlib.sha256(b"").hexdigest()

Sleep = Callable[[float], Awaitable[None]]


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def sigv4_headers(
    method: str,
    url: str,
    *,
    payload_hash: str,
    access_key: str,
    secret_key: str,
    region: str,
    amz_date: str,
    extra_headers: dict[str, str] | None = None,
    service: str = "s3",
) -> dict[str, str]:
    """Headers (incl. ``Authorization``) for one SigV4-signed request (pure)."""
    parsed = httpx.URL(url)
    host = parsed.host if parsed.port is None else f"{parsed.host}:{parsed.port}"
    path = parsed.raw_path.decode("ascii").split("?", 1)[0] or "/"
    query = "&".join(
        f"{quote(k, safe='~')}={quote(v, safe='~')}" for k, v in sorted(parsed.params.multi_items())
    )
    headers = {"host": host, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date}
    headers |= {k.lower(): v for k, v in (extra_headers or {}).items()}
    names = sorted(headers)
    canonical_headers = "".join(f"{name}:{headers[name].strip()}\n" for name in names)
    signed = ";".join(names)
    request = "\n".join([method, path, query, canonical_headers, signed, payload_hash])
    scope = f"{amz_date[:8]}/{region}/{service}/aws4_request"
    digest = hashlib.sha256(request.encode("utf-8")).hexdigest()
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, digest])
    key = _hmac(f"AWS4{secret_key}".encode(), amz_date[:8])
    for part in (region, service, "aws4_request"):
        key = _hmac(key, part)
    signature = hmac.new(key, to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    auth = f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed}, "
    out = {k: v for k, v in headers.items() if k != "host"}
    out["Authorization"] = auth + f"Signature={signature}"
    return out


async def send_with_retries(
    send: Callable[[], Awaitable[httpx.Response]], *, sleep: Sleep, what: str
) -> httpx.Response:
    """Retry 5xx, 429 and transport errors 3 times (2, 4, 8 s); 4xx is returned at once."""
    for attempt in range(len(BACKOFF_S) + 1):
        try:
            response = await send()
        except httpx.TransportError as exc:
            if attempt == len(BACKOFF_S):
                raise UpstreamError(f"{what} unreachable", code="upstream_unavailable") from exc
        else:
            if response.status_code < 500 and response.status_code != 429:
                return response
            if attempt == len(BACKOFF_S):
                raise UpstreamError(f"{what} returned {response.status_code}")
        await sleep(BACKOFF_S[attempt])
    raise AssertionError("unreachable")


class S3Client:
    def __init__(
        self,
        *,
        endpoint: str,
        region: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.region, self.bucket = region, bucket
        self._keys = (access_key, secret_key)
        self._transport = transport
        self._sleep = sleep

    def _url(self, key: str = "") -> str:
        return f"{self.endpoint}/{self.bucket}" + (f"/{quote(key, safe='/~')}" if key else "")

    async def _send(
        self, method: str, url: str, body: bytes = b"", params: dict[str, str] | None = None
    ) -> httpx.Response:
        full = str(httpx.URL(url, params=params or {}))
        payload_hash = hashlib.sha256(body).hexdigest() if body else _EMPTY_SHA
        amz_date = clock.now().strftime("%Y%m%dT%H%M%SZ")
        headers = sigv4_headers(
            method,
            full,
            payload_hash=payload_hash,
            access_key=self._keys[0],
            secret_key=self._keys[1],
            region=self.region,
            amz_date=amz_date,
        )
        async with httpx.AsyncClient(transport=self._transport, timeout=30.0) as client:
            return await send_with_retries(
                lambda: client.request(method, full, content=body or None, headers=headers),
                sleep=self._sleep,
                what="object storage",
            )

    async def put_object(self, key: str, data: bytes) -> None:
        response = await self._send("PUT", self._url(key), data)
        if response.status_code >= 300:
            raise UpstreamError(f"object storage PUT returned {response.status_code}")

    async def get_object(self, key: str) -> bytes | None:
        response = await self._send("GET", self._url(key))
        if response.status_code == 404:
            return None
        if response.status_code >= 300:
            raise UpstreamError(f"object storage GET returned {response.status_code}")
        return response.content

    async def list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        token: str | None = None
        while True:
            params = {"list-type": "2", "prefix": prefix}
            if token:
                params["continuation-token"] = token
            response = await self._send("GET", self._url(), params=params)
            if response.status_code >= 300:
                raise UpstreamError(f"object storage LIST returned {response.status_code}")
            root = ElementTree.fromstring(response.content)
            keys += [el.text or "" for el in root.iter(f"{_NS}Key")]
            token = root.findtext(f"{_NS}NextContinuationToken")
            if root.findtext(f"{_NS}IsTruncated") != "true" or not token:
                return keys

    async def upload_dir(self, local: Path, prefix: str) -> int:
        files = sorted(p for p in local.rglob("*") if p.is_file() and not p.is_symlink())
        for path in files:
            await self.put_object(prefix + path.relative_to(local).as_posix(), path.read_bytes())
        return len(files)

    async def download_prefix(self, prefix: str, local: Path) -> int:
        root = local.resolve()
        count = 0
        for key in await self.list_keys(prefix):
            target = (root / key[len(prefix) :]).resolve()
            if not key.startswith(prefix) or not target.is_relative_to(root) or target == root:
                continue
            data = await self.get_object(key)
            if data is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            count += 1
        return count
