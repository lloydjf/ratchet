"""Pydantic models for the fictional world's JSON fixtures (see ``WORLD_FORMAT.md``).

Item models allow extra fields so world authors can add detail that flows through to tool
results; the per-file root models forbid unknown top-level keys so a typo fails at load.
Times are ISO 8601 strings with an offset; money is a JSON number.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "Comment",
    "Issue",
    "PullRequest",
    "Repo",
    "GithubWorld",
    "Message",
    "Mailbox",
    "SlackMessage",
    "SlackWorld",
    "ReceiptLine",
    "Receipt",
    "Sheet",
    "SheetsWorld",
    "Interval",
    "CalendarEvent",
    "CalendarWorld",
    "FxWorld",
    "Invoice",
    "Payment",
    "PaymentsWorld",
    "Page",
    "SearchResult",
    "WebWorld",
    "Commit",
    "GitWorld",
    "Manifest",
]


class _Item(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class _Root(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- github.json ------------------------------------------------------------------------
class Comment(_Item):
    comment_id: int
    author: str
    body: str
    created_at: str


class Issue(_Item):
    number: int
    title: str
    body: str = ""
    state: Literal["open", "closed"] = "open"
    state_reason: str | None = None
    labels: list[str] = Field(default_factory=list)
    author: str = ""
    assignees: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str
    closed_at: str | None = None
    comments: list[Comment] = Field(default_factory=list)


class PullRequest(_Item):
    number: int
    title: str
    body: str = ""
    head: str
    base: str = "main"
    state: Literal["open", "closed", "merged"] = "open"
    author: str = ""
    created_at: str
    merged_at: str | None = None


class Repo(_Item):
    description: str = ""
    labels: list[str] = Field(default_factory=list)  # known labels; informational
    issues: list[Issue] = Field(default_factory=list)
    pulls: list[PullRequest] = Field(default_factory=list)


class GithubWorld(_Root):
    repos: dict[str, Repo] = Field(default_factory=dict)  # "acme/widgets" -> Repo


# --- mailbox.json, slack.json ---------------------------------------------------------
class Message(_Item):
    message_id: str
    sender: str = Field(alias="from")
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    subject: str = ""
    body: str = ""
    date: str
    labels: list[str] = Field(default_factory=list)
    thread_id: str | None = None


class Mailbox(_Root):
    address: str = "agent@example.com"
    messages: list[Message] = Field(default_factory=list)
    outbox: list[Message] = Field(default_factory=list)


class SlackMessage(_Item):
    author: str
    text: str
    posted_at: str


class SlackWorld(_Root):
    channels: dict[str, list[SlackMessage]] = Field(default_factory=dict)  # "#ops" -> messages


# --- receipts/<file>.json, sheets.json --------------------------------------------------
class ReceiptLine(_Item):
    description: str
    quantity: float = 1
    unit_price: float | None = None
    amount: float


class Receipt(_Item):
    merchant: str
    date: str  # YYYY-MM-DD
    currency: str
    lines: list[ReceiptLine] = Field(default_factory=list)
    subtotal: float
    vat: float | None = None
    vat_rate: float | None = None
    total: float


class Sheet(_Item):
    header: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)


class SheetsWorld(_Root):
    sheets: dict[str, Sheet] = Field(default_factory=dict)


# --- calendar.json ---------------------------------------------------------------------
class Interval(_Item):
    start: str
    end: str


class CalendarEvent(_Item):
    event_id: str
    title: str
    start: str
    end: str
    attendees: list[str] = Field(default_factory=list)
    calendar: str = "primary"


class CalendarWorld(_Root):
    events: list[CalendarEvent] = Field(default_factory=list)
    busy: dict[str, list[Interval]] = Field(default_factory=dict)  # extra busy blocks per email


# --- fx.json, payments.json -------------------------------------------------------------
class FxWorld(_Root):
    rates: dict[str, dict[str, float]] = Field(default_factory=dict)  # "EUR/USD" -> date -> rate


class Invoice(_Item):
    amount: str
    currency: str = "EUR"
    payee: str = ""
    status: Literal["open", "paid"] = "open"


class Payment(_Item):
    payment_ref: str
    invoice: str
    amount: str
    currency: str
    paid_at: str


class PaymentsWorld(_Root):
    invoices: dict[str, Invoice] = Field(default_factory=dict)
    payments: list[Payment] = Field(default_factory=list)


# --- web.json, git.json, world.json ------------------------------------------------------
class Page(_Item):
    title: str
    text: str
    published: str | None = None


class SearchResult(_Item):
    title: str
    url: str
    snippet: str = ""


class WebWorld(_Root):
    pages: dict[str, Page] = Field(default_factory=dict)  # url -> page
    search: dict[str, list[SearchResult]] = Field(default_factory=dict)  # query -> results


class Commit(_Item):
    sha: str
    date: str
    author: str
    message: str
    tags: list[str] = Field(default_factory=list)


class GitWorld(_Root):
    repos: dict[str, list[Commit]] = Field(default_factory=dict)  # newest first, like git log


class Manifest(_Root):
    name: str = "world"
    description: str = ""
    frozen_time: str | None = None  # default clock for time.now in record mode
