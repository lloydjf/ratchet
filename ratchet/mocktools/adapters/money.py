"""Receipts, spreadsheets, FX rates and payments over the world fixtures."""

from __future__ import annotations

from ratchet.errors import ConflictError
from ratchet.mocktools.adapters.base import (
    Adapter,
    Args,
    Result,
    not_found,
    opt_str,
    req_str,
    rows_arg,
)
from ratchet.mocktools.world import CallContext, ToolArgumentError, World, WorldState
from ratchet.mocktools.world_models import Payment, Sheet

__all__ = ["ADAPTERS", "lookup_rate"]


def parse_receipt(state: WorldState, args: Args, ctx: CallContext) -> Result:
    file = req_str(args, "file")
    for key in World.receipt_keys(file):
        receipt = state.receipts.get(key)
        if receipt is not None:
            return {"file": file, **receipt.model_dump(mode="json")}
    raise not_found("receipt", file)


def read_sheet(state: WorldState, args: Args, ctx: CallContext) -> Result:
    name = req_str(args, "sheet").strip()
    sheet = state.sheets.sheets.get(name)
    if sheet is None:
        raise not_found("sheet", name)
    return {"sheet": name, "header": list(sheet.header), "rows": [list(r) for r in sheet.rows]}


def append_rows(state: WorldState, args: Args, ctx: CallContext) -> Result:
    name = req_str(args, "sheet").strip()
    rows = rows_arg(args)
    # A missing sheet is created, like a spreadsheet tab added on first write.
    sheet = state.sheets.sheets.setdefault(name, Sheet())
    sheet.rows.extend(rows)
    return {"sheet": name, "appended": len(rows), "total_rows": len(sheet.rows)}


def _currency(args: Args, name: str) -> str:
    value = req_str(args, name).strip().upper()
    if len(value) != 3 or not value.isalpha():
        raise ToolArgumentError(f"{name} must be an ISO 4217 code")
    return value


def _on_or_before(series: dict[str, float], day: str) -> tuple[str, float] | None:
    eligible = sorted(d for d in series if d <= day)
    return (eligible[-1], series[eligible[-1]]) if eligible else None


def lookup_rate(
    rates: dict[str, dict[str, float]], base: str, quote: str, day: str
) -> tuple[str, float] | None:
    """Rate on ``day`` or the latest earlier date; falls back to the inverse pair."""
    if base == quote:
        return day, 1.0
    direct = rates.get(f"{base}/{quote}")
    if direct is not None and (found := _on_or_before(direct, day)) is not None:
        return found
    inverse = rates.get(f"{quote}/{base}")
    if inverse is not None and (found := _on_or_before(inverse, day)) is not None:
        return found[0], round(1.0 / found[1], 6)
    return None


def rate(state: WorldState, args: Args, ctx: CallContext) -> Result:
    base, quote = _currency(args, "base"), _currency(args, "quote")
    day = opt_str(args, "date") or ctx.now.date().isoformat()
    found = lookup_rate(state.fx.rates, base, quote, day.strip())
    if found is None:
        raise not_found("rate", f"{base}/{quote} on {day}")
    return {"base": base, "quote": quote, "date": found[0], "rate": found[1]}


def pay(state: WorldState, args: Args, ctx: CallContext) -> Result:
    ref = req_str(args, "invoice").strip()
    amount = req_str(args, "amount").strip()
    invoices = state.payments.invoices
    invoice = invoices.get(ref)
    if invoices and invoice is None:
        raise not_found("invoice", ref)
    if invoice is not None and invoice.status == "paid":
        raise ConflictError("invoice already paid", code="already_paid")
    default_currency = invoice.currency if invoice is not None else "EUR"
    currency = (opt_str(args, "currency") or default_currency).strip().upper()
    if invoice is not None:
        invoice.status = "paid"
    payment = Payment(
        payment_ref=f"pay_{len(state.payments.payments) + 1:04d}",
        invoice=ref,
        amount=amount,
        currency=currency,
        paid_at=ctx.now_iso,
    )
    state.payments.payments.append(payment)
    return {"status": "paid", **payment.model_dump(mode="json")}


ADAPTERS: dict[str, Adapter] = {
    "receipts.parse": parse_receipt,
    "sheets.read": read_sheet,
    "sheets.append": append_rows,
    "finance.rate": rate,
    "payments.pay": pay,
}
