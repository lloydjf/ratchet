"""The fictional, deterministic world served to the agent in record mode (no real data).

A world is a directory of JSON fixtures (format: ``WORLD_FORMAT.md``): a demo org
``acme``, people at ``example.com``, receipts, sheets, a calendar, FX rates, web pages and
git logs. Nothing in it is real. ``World.load(path)`` parses and validates every file;
each run gets its own deep copy of the state (``World.state(run_id)``), so writes
(labels, closed issues, appended rows, sent mail) are visible to later calls of the same
run and never leak into another run. Adapters in ``ratchet.mocktools.adapters`` read and
mutate a :class:`WorldState`.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from ratchet import clock
from ratchet.errors import ConfigError, RatchetError
from ratchet.mocktools.world_models import (
    CalendarWorld,
    FxWorld,
    GithubWorld,
    GitWorld,
    Mailbox,
    Manifest,
    PaymentsWorld,
    Receipt,
    SheetsWorld,
    SlackWorld,
    WebWorld,
)

__all__ = ["World", "WorldState", "CallContext", "ToolArgumentError", "WORLD_FILES", "blank_state"]

M = TypeVar("M", bound=BaseModel)

WORLD_FILES: dict[str, type[BaseModel]] = {
    "world.json": Manifest,
    "github.json": GithubWorld,
    "mailbox.json": Mailbox,
    "slack.json": SlackWorld,
    "sheets.json": SheetsWorld,
    "calendar.json": CalendarWorld,
    "fx.json": FxWorld,
    "payments.json": PaymentsWorld,
    "web.json": WebWorld,
    "git.json": GitWorld,
}
_RECEIPT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,127}$")


class ToolArgumentError(RatchetError):
    """A tool was called with arguments the world cannot interpret."""

    code = "bad_args"
    http_status = 400


@dataclass(frozen=True)
class CallContext:
    """Per-call facts an adapter may use: the run and the clock it should read."""

    run_id: str
    now: datetime

    @property
    def now_iso(self) -> str:
        return clock.iso(self.now).replace("+00:00", "Z")


@dataclass
class WorldState:
    """One run's mutable view of the world."""

    manifest: Manifest
    github: GithubWorld
    mailbox: Mailbox
    slack: SlackWorld
    sheets: SheetsWorld
    calendar: CalendarWorld
    fx: FxWorld
    payments: PaymentsWorld
    web: WebWorld
    git: GitWorld
    receipts: dict[str, Receipt]

    def copy(self) -> WorldState:
        return WorldState(
            manifest=self.manifest.model_copy(deep=True),
            github=self.github.model_copy(deep=True),
            mailbox=self.mailbox.model_copy(deep=True),
            slack=self.slack.model_copy(deep=True),
            sheets=self.sheets.model_copy(deep=True),
            calendar=self.calendar.model_copy(deep=True),
            fx=self.fx.model_copy(deep=True),
            payments=self.payments.model_copy(deep=True),
            web=self.web.model_copy(deep=True),
            git=self.git.model_copy(deep=True),
            receipts={name: r.model_copy(deep=True) for name, r in self.receipts.items()},
        )


def blank_state() -> WorldState:
    """An empty world (every fixture at its defaults)."""
    return WorldState(
        manifest=Manifest(),
        github=GithubWorld(),
        mailbox=Mailbox(),
        slack=SlackWorld(),
        sheets=SheetsWorld(),
        calendar=CalendarWorld(),
        fx=FxWorld(),
        payments=PaymentsWorld(),
        web=WebWorld(),
        git=GitWorld(),
        receipts={},
    )


def _load_file(path: Path, model: type[M]) -> M:
    if not path.exists():
        return model()
    try:
        return model.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (ValidationError, json.JSONDecodeError) as exc:
        raise ConfigError(
            f"invalid world fixture {path.name}: {exc}",
            code="world_invalid",
            context={"file": path.name},
        ) from exc


def _load_receipts(directory: Path) -> dict[str, Receipt]:
    receipts: dict[str, Receipt] = {}
    if not directory.is_dir():
        return receipts
    for path in sorted(directory.glob("*.json")):
        receipts[path.stem] = _load_file(path, Receipt)
    return receipts


class World:
    """A loaded world directory plus per-run copies of its state."""

    def __init__(self, base: WorldState, root: Path | None = None) -> None:
        self._base = base
        self.root = root
        self._runs: dict[str, WorldState] = {}
        self._lock = threading.Lock()

    @classmethod
    def load(cls, path: Path) -> World:
        if not path.is_dir():
            raise ConfigError(f"world directory {path} not found", code="world_missing")
        state = WorldState(
            manifest=_load_file(path / "world.json", Manifest),
            github=_load_file(path / "github.json", GithubWorld),
            mailbox=_load_file(path / "mailbox.json", Mailbox),
            slack=_load_file(path / "slack.json", SlackWorld),
            sheets=_load_file(path / "sheets.json", SheetsWorld),
            calendar=_load_file(path / "calendar.json", CalendarWorld),
            fx=_load_file(path / "fx.json", FxWorld),
            payments=_load_file(path / "payments.json", PaymentsWorld),
            web=_load_file(path / "web.json", WebWorld),
            git=_load_file(path / "git.json", GitWorld),
            receipts=_load_receipts(path / "receipts"),
        )
        return cls(state, root=path)

    @property
    def name(self) -> str:
        return self._base.manifest.name

    @property
    def frozen_time(self) -> datetime | None:
        raw = self._base.manifest.frozen_time
        return clock.parse(raw) if raw else None

    def state(self, run_id: str) -> WorldState:
        """The run's own state, copied from the loaded fixtures on first use."""
        with self._lock:
            current = self._runs.get(run_id)
            if current is None:
                current = self._base.copy()
                self._runs[run_id] = current
            return current

    def reset(self, run_id: str | None = None) -> None:
        """Forget one run's mutations (or every run's)."""
        with self._lock:
            if run_id is None:
                self._runs.clear()
            else:
                self._runs.pop(run_id, None)

    @staticmethod
    def receipt_keys(file: str) -> tuple[str, ...]:
        """Fixture keys tried for a ``receipts.parse`` file: ``taxi.pdf`` then ``taxi``."""
        name = file.strip().rsplit("/", 1)[-1]
        if not _RECEIPT_NAME.match(name):
            raise ToolArgumentError("receipt file name is not valid", code="bad_args")
        stem = name.rsplit(".", 1)[0] if "." in name else name
        return (name, stem) if stem != name else (name,)
