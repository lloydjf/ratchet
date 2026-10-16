"""Simulated ``expense-report`` policy: parse each receipt → one ``sheets.append`` → report.

Directives, read from the skill text:

=========================  ===========================================  ======================
directive                  regex (case-insensitive)                     effect
=========================  ===========================================  ======================
``report_vat``             ``"`VAT: <amount> <currency>`"``             a ``VAT:`` line per
                                                                        receipt that has VAT
``vat_from_summary``       ``use the `vat` value from the receipt's     VAT falls back to the
                           `summary` block``                            ``summary`` block
``vat_from_lines_only``    ``skip the summary block``                   VAT and total come
                                                                        from line items only
``sheet``                  ``sheets.append ... "sheet": "<name>"``      target sheet
``columns``                ``columns `<a, b, c>```                      row layout
=========================  ===========================================  ======================

Receipt files are taken from the task text (``name.pdf|jpg|jpeg|png``). A layout-B receipt
prints VAT only in its ``summary`` block, so ``vat_from_lines_only`` drops its VAT line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from ratchet.sim.agent.base import Conversation, PlannedCall, Reply, ToolUse
from ratchet.sim.text import money

__all__ = ["NAME", "Directives", "Parsed", "directives", "receipt_files", "read", "policy"]

NAME = "expense-report"
REPORT_VAT = re.compile(r"`VAT:\s*<amount>\s*<currency>`", re.IGNORECASE)
VAT_FROM_SUMMARY = re.compile(r"use the `?vat`? value from the receipt's `?summary`? block", re.I)
VAT_FROM_LINES_ONLY = re.compile(r"skip the summary block", re.IGNORECASE)
SHEET = re.compile(r"`sheets\.append`[^\n]*?\"sheet\":\s*\"([^\"]+)\"", re.IGNORECASE)
COLUMNS = re.compile(r"columns\s*`([^`]+)`", re.IGNORECASE)
RECEIPT_FILE = re.compile(r"\b([A-Za-z0-9][A-Za-z0-9._-]*\.(?:pdf|jpe?g|png))\b", re.IGNORECASE)
VAT_LINE = re.compile(r"^\s*(?:VAT|MwSt|USt|TVA|IVA)\b", re.IGNORECASE)
DEFAULT_COLUMNS = ("date", "merchant", "total", "currency", "vat", "file")


@dataclass(frozen=True)
class Directives:
    report_vat: bool = False
    vat_from_summary: bool = False
    vat_from_lines_only: bool = False
    sheet: str = "Expenses"
    columns: tuple[str, ...] = DEFAULT_COLUMNS


@dataclass(frozen=True)
class Parsed:
    file: str
    merchant: str
    date: str
    currency: str
    total: Decimal
    vat: Decimal | None
    error: str | None = None


def directives(body: str) -> Directives:
    sheet = SHEET.search(body)
    columns = COLUMNS.search(body)
    return Directives(
        report_vat=bool(REPORT_VAT.search(body)),
        vat_from_summary=bool(VAT_FROM_SUMMARY.search(body)),
        vat_from_lines_only=bool(VAT_FROM_LINES_ONLY.search(body)),
        sheet=sheet.group(1) if sheet else "Expenses",
        columns=tuple(c.strip().lower() for c in columns.group(1).split(","))
        if columns
        else DEFAULT_COLUMNS,
    )


def receipt_files(task: str) -> list[str]:
    return list(dict.fromkeys(RECEIPT_FILE.findall(task)))


def _dec(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value))
    except ArithmeticError:
        return None


def _lines(result: dict[str, object]) -> list[dict[str, object]]:
    raw = result.get("lines")
    return [line for line in raw if isinstance(line, dict)] if isinstance(raw, list) else []


def read(use: ToolUse, d: Directives) -> Parsed:
    r = use.result
    file = str(use.args.get("file", ""))
    if use.failed:
        return Parsed(file, "", "", "", Decimal(0), None, error="receipt not found")
    if d.vat_from_lines_only:
        amounts = [_dec(line.get("amount")) or Decimal(0) for line in _lines(r)]
        total = sum(amounts, Decimal(0))
        vat_lines = [
            _dec(line.get("amount")) or Decimal(0)
            for line in _lines(r)
            if VAT_LINE.match(str(line.get("description", "")))
        ]
        vat = sum(vat_lines, Decimal(0)) if vat_lines else None
    else:
        total = _dec(r.get("total")) or Decimal(0)
        vat = _dec(r.get("vat"))
        summary = r.get("summary")
        if vat is None and d.vat_from_summary and isinstance(summary, dict):
            vat = _dec(summary.get("vat"))
    merchant, date, currency = (str(r.get(k, "")) for k in ("merchant", "date", "currency"))
    return Parsed(file, merchant, date, currency, total, vat)


def _cell(parsed: Parsed, column: str) -> str:
    values = {
        "date": parsed.date,
        "merchant": parsed.merchant,
        "total": money(parsed.total),
        "currency": parsed.currency,
        "vat": money(parsed.vat) if parsed.vat is not None else "",
        "file": parsed.file,
    }
    return values.get(column, "")


def _report(receipts: list[Parsed], d: Directives) -> str:
    blocks: list[str] = []
    for p in receipts:
        if p.error:
            blocks.append(f"Not filed: {p.file} ({p.error})")
            continue
        lines = [
            f"Merchant: {p.merchant}",
            f"Date: {p.date}",
            f"Total: {money(p.total)} {p.currency}",
        ]
        if d.report_vat and p.vat is not None:
            lines.append(f"VAT: {money(p.vat)} {p.currency}")
        blocks.append("\n".join(lines))
    good = [p for p in receipts if not p.error]
    currency = good[0].currency if good else ""
    grand = sum((p.total for p in good), Decimal(0))
    tail = [f"Grand total: {money(grand)} {currency}".rstrip()]
    tail.append(f"Filed {len(good)} receipt{'s' if len(good) != 1 else ''} to the {d.sheet} sheet.")
    return "\n\n".join([*blocks, "\n".join(tail)])


def policy(conv: Conversation, body: str) -> Reply:
    files = receipt_files(conv.task)
    if not files:
        return Reply("Which receipt files should I file? Please name them, for example taxi.pdf.")
    d = directives(body)
    parsed_files = {str(u.args.get("file", "")) for u in conv.used("receipts.parse")}
    todo = [f for f in files if f not in parsed_files]
    if todo:
        return Reply(calls=[PlannedCall("receipts.parse", {"file": f}) for f in todo])
    by_file = {str(u.args.get("file", "")): u for u in conv.used("receipts.parse")}
    receipts = [read(by_file[f], d) for f in files]
    rows = [[_cell(p, c) for c in d.columns] for p in receipts if not p.error]
    if rows and not conv.called("sheets.append"):
        return Reply(calls=[PlannedCall("sheets.append", {"sheet": d.sheet, "rows": rows})])
    return Reply(_report(receipts, d))
