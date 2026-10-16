"""Simulated ``fx-expense-converter`` policy: ``finance.rate`` → convert → append → report.

Directives, read from the skill text:

==================  ==========================================  ==============================
directive           regex (case-insensitive)                    effect
==================  ==========================================  ==============================
``quote``           ``"quote": "<CUR>"``                        target currency (default USD)
``sheet``           ``sheets.append ... "sheet": "<name>"``     target sheet
``columns``         ``columns `<a, b, c>```                     row layout
``rate_decimals``   ``rate with <N> decimals``                  rate precision (default 4)
``rate_line``       ``Rate used: 1 <currency> = <rate>``        final ``Rate used:`` line
==================  ==========================================  ==============================

Expenses come from the task text as ``<description> <amount> <CUR>`` items after the last
colon; the date is the first ``YYYY-MM-DD`` in the task, else the "Current date". The rate is
whatever ``finance.rate`` returns, so a moved world changes every converted figure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from ratchet.sim.agent.base import Conversation, PlannedCall, Reply
from ratchet.sim.text import fixed, money

__all__ = ["NAME", "Directives", "Expense", "directives", "expenses", "policy"]

NAME = "fx-expense-converter"
QUOTE = re.compile(r"\"quote\":\s*\"([A-Z]{3})\"")
SHEET = re.compile(r"`sheets\.append`[^\n]*?\"sheet\":\s*\"([^\"]+)\"", re.IGNORECASE)
COLUMNS = re.compile(r"columns\s*`([^`]+)`", re.IGNORECASE)
RATE_DECIMALS = re.compile(r"rate with (\d+) decimals", re.IGNORECASE)
RATE_LINE = re.compile(r"Rate used: 1 <currency> = <rate>", re.IGNORECASE)
DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
ITEM = re.compile(r"^(?:and\s+)?(.+?)\s+(\d+(?:\.\d+)?)\s*([A-Z]{3})\.?$")
DEFAULT_COLUMNS = ("date", "description", "amount", "currency", "usd", "rate")


@dataclass(frozen=True)
class Directives:
    quote: str = "USD"
    sheet: str = "Expenses USD"
    columns: tuple[str, ...] = DEFAULT_COLUMNS
    rate_decimals: int = 4
    rate_line: bool = False


@dataclass(frozen=True)
class Expense:
    description: str
    amount: Decimal
    currency: str


def directives(body: str) -> Directives:
    quote, sheet = QUOTE.search(body), SHEET.search(body)
    columns, places = COLUMNS.search(body), RATE_DECIMALS.search(body)
    return Directives(
        quote=quote.group(1) if quote else "USD",
        sheet=sheet.group(1) if sheet else "Expenses USD",
        columns=tuple(c.strip().lower() for c in columns.group(1).split(","))
        if columns
        else DEFAULT_COLUMNS,
        rate_decimals=int(places.group(1)) if places else 4,
        rate_line=bool(RATE_LINE.search(body)),
    )


def expenses(task: str) -> list[Expense]:
    tail = task.rsplit(":", 1)[-1]
    found: list[Expense] = []
    for part in re.split(r",|;|\band\b", tail):
        match = ITEM.match(part.strip())
        if match:
            found.append(Expense(match.group(1).strip(), Decimal(match.group(2)), match.group(3)))
    return found


def _rate(conv: Conversation) -> tuple[Decimal, str] | None:
    uses = conv.used("finance.rate")
    if not uses or uses[-1].failed:
        return None
    result = uses[-1].result
    try:
        return Decimal(str(result.get("rate"))), str(result.get("date", ""))
    except ArithmeticError:
        return None


def _report(items: list[Expense], rate: Decimal, day: str, d: Directives) -> str:
    base = items[0].currency
    converted = [Decimal(money(e.amount * rate)) for e in items]
    lines = [
        f"{e.description[:1].upper()}{e.description[1:]}: {money(e.amount)} {e.currency} = "
        f"{money(usd)} {d.quote}"
        for e, usd in zip(items, converted, strict=True)
    ]
    total = sum((e.amount for e in items), Decimal(0))
    lines.append(f"Total: {money(total)} {base} = {money(sum(converted, Decimal(0)))} {d.quote}")
    if d.rate_line:
        lines.append(f"Rate used: 1 {base} = {fixed(rate, d.rate_decimals)} {d.quote} ({day})")
    return "\n".join(lines)


def _row(e: Expense, rate: Decimal, day: str, d: Directives) -> list[str]:
    values = {
        "date": day,
        "description": e.description,
        "amount": money(e.amount),
        "currency": e.currency,
        "usd": money(e.amount * rate),
        "rate": fixed(rate, d.rate_decimals),
    }
    return [values.get(c, "") for c in d.columns]


def policy(conv: Conversation, body: str) -> Reply:
    items = expenses(conv.task)
    if not items:
        return Reply("Which expenses should I convert? List them as <description> <amount> EUR.")
    d = directives(body)
    found = DATE.search(conv.task)
    day = found.group(1) if found else conv.now.date().isoformat()
    base = items[0].currency
    if not conv.called("finance.rate"):
        args: dict[str, object] = {"base": base, "quote": d.quote, "date": day}
        return Reply(calls=[PlannedCall("finance.rate", args)])
    rate = _rate(conv)
    if rate is None:
        return Reply(f"I could not find a {base}/{d.quote} rate for {day}, so nothing was logged.")
    value, rate_day = rate
    if not conv.called("sheets.append"):
        rows = [_row(e, value, day, d) for e in items]
        return Reply(calls=[PlannedCall("sheets.append", {"sheet": d.sheet, "rows": rows})])
    return Reply(_report(items, value, rate_day or day, d))
