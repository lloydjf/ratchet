"""Mail and chat adapters over ``mailbox.json`` and ``slack.json``."""

from __future__ import annotations

from ratchet.mocktools.adapters.base import Adapter, Args, Result, req_str, str_list
from ratchet.mocktools.world import CallContext, WorldState
from ratchet.mocktools.world_models import Message, SlackMessage

__all__ = ["ADAPTERS", "message_matches"]

_FIELDS = ("from", "to", "subject", "label")


def _field_text(message: Message, field: str) -> str:
    if field == "from":
        return message.sender
    if field == "to":
        return " ".join(message.to + message.cc)
    if field == "subject":
        return message.subject
    return " ".join(message.labels)


def message_matches(message: Message, query: str) -> bool:
    """Gmail-ish query: ``from:``/``to:``/``subject:``/``label:`` terms plus free words."""
    haystack = f"{message.subject}\n{message.body}".casefold()
    for term in query.split():
        field, sep, value = term.partition(":")
        if sep and field.casefold() in _FIELDS and value:
            if value.casefold() not in _field_text(message, field.casefold()).casefold():
                return False
        elif term.casefold() not in haystack:
            return False
    return True


def _message_view(message: Message) -> Result:
    return message.model_dump(mode="json", by_alias=True)


def search(state: WorldState, args: Args, ctx: CallContext) -> Result:
    query = req_str(args, "query")
    found = [m for m in state.mailbox.messages if message_matches(m, query)]
    found.sort(key=lambda m: (m.date, m.message_id), reverse=True)
    return {"query": query, "messages": [_message_view(m) for m in found]}


def send(state: WorldState, args: Args, ctx: CallContext) -> Result:
    outbox = state.mailbox.outbox
    message = Message.model_validate(
        {
            "message_id": f"msg_out_{len(outbox) + 1:04d}",
            "from": state.mailbox.address,
            "to": str_list(args, "to", required=True),
            "cc": str_list(args, "cc"),
            "subject": req_str(args, "subject"),
            "body": req_str(args, "body"),
            "date": ctx.now_iso,
            "labels": ["SENT"],
        }
    )
    outbox.append(message)
    return {"message_id": message.message_id, "status": "sent", "to": message.to}


def slack_post(state: WorldState, args: Args, ctx: CallContext) -> Result:
    channel = req_str(args, "channel").strip()
    if not channel.startswith("#"):
        channel = f"#{channel}"
    messages = state.slack.channels.setdefault(channel, [])
    messages.append(
        SlackMessage(author="ratchet-agent", text=req_str(args, "text"), posted_at=ctx.now_iso)
    )
    return {"channel": channel, "posted": True, "message_number": len(messages)}


ADAPTERS: dict[str, Adapter] = {
    "gmail.search": search,
    "gmail.send": send,
    "slack.post": slack_post,
}
