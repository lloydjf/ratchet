"""In-process sidecars: run an ASGI app (proxy, mock tools) on an ephemeral port.

The worker starts the capture proxy and the mock tool server *inside its own process*
so the real Token Factory key only ever lives in this process's settings, never
in the sandbox. Each app gets a uvicorn server on its own thread, bound to a
socket the worker opened on ``host:0`` so the port is known before the harness starts.
"""

from __future__ import annotations

import errno
import socket
import threading
import time

import uvicorn
from starlette.types import ASGIApp

from ratchet.errors import SandboxError

__all__ = ["ServerThread"]

_START_TIMEOUT_S = 15.0


class ServerThread:
    """``with ServerThread(app, "127.0.0.1") as server: server.url`` — start/stop a server."""

    def __init__(
        self, app: ASGIApp, host: str, *, name: str = "sidecar", hint: str | None = None
    ) -> None:
        self._app = app
        self.host = host
        self.name = name
        self.hint = hint  # appended when ``host`` is not a local address (EADDRNOTAVAIL)
        self.port = 0
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}"

    def start(self) -> ServerThread:
        family = socket.AF_INET6 if ":" in self.host else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self.host, 0))
        except OSError as exc:
            sock.close()
            message = f"could not bind sidecar {self.name!r} on {self.host}: {exc.strerror or exc}"
            if self.hint and exc.errno == errno.EADDRNOTAVAIL:
                message = f"{message}; {self.hint}"
            raise SandboxError(
                message, code="sandbox_failed", context={"host": self.host, "sidecar": self.name}
            ) from exc
        self.port = int(sock.getsockname()[1])
        config = uvicorn.Config(
            self._app,
            log_config=None,
            log_level="warning",
            access_log=False,
            lifespan="auto",
            timeout_graceful_shutdown=2,
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(
            target=server.run, kwargs={"sockets": [sock]}, name=f"ratchet-{self.name}", daemon=True
        )
        self._server, self._thread = server, thread
        thread.start()
        deadline = time.monotonic() + _START_TIMEOUT_S
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                self.stop()
                raise SandboxError(f"the {self.name} sidecar did not start", code="sandbox_failed")
            time.sleep(0.01)
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=10.0)
            if self._thread.is_alive() and self._server is not None:
                self._server.force_exit = True
                self._thread.join(timeout=5.0)
        self._server, self._thread = None, None

    def __enter__(self) -> ServerThread:
        return self.start()

    def __exit__(self, *_: object) -> None:
        self.stop()
