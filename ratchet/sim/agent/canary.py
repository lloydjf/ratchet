"""Simulated answers when no skill is injected: the three canary task shapes, else a polite
deterministic reply with no tool calls.

================  ==============================================  ==============================
task              regex (case-insensitive)                         answer
================  ==============================================  ==============================
formal rewrite    ``rewrite this sentence formally: <text>``       rule-based rewrite (``FORMAL``)
percent           ``what is <p>% of <n>``                          ``<p>% of <n> is <result>.``
summary           ``summarize this note: <text>``                  ``Summary: <first sentence>``
anything else     —                                                :data:`UNKNOWN_REPLY`
================  ==============================================  ==============================
"""

from __future__ import annotations

import re
from decimal import Decimal

from ratchet.sim.agent.base import Conversation, Reply
from ratchet.sim.text import first_sentence

__all__ = ["FORMAL", "UNKNOWN_REPLY", "formal", "percent", "policy"]

REWRITE = re.compile(
    r"rewrite (?:this|the following)\s*(?:sentence|text|message)?\s*"
    r"(?:formally|more formally|in a formal tone)\s*:\s*(.+)$",
    re.IGNORECASE | re.DOTALL,
)
PERCENT = re.compile(r"what is (\d+(?:\.\d+)?)\s*%\s*of\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
SUMMARIZE = re.compile(
    r"summari[sz]e (?:this|the following)\s*(?:note|text|message|paragraph)?\s*:\s*(.+)$",
    re.IGNORECASE | re.DOTALL,
)
UNKNOWN_REPLY = (
    "I can help with that. Could you tell me a little more about what you would like me to do?"
)
# Informal → formal, applied in order (whole words, case-insensitive).
FORMAL: tuple[tuple[str, str], ...] = (
    (r"hey guys|hi guys|hey all|hi all", "Hello everyone"),
    (r"hey|hiya", "Hello"),
    (r"guys|folks", "everyone"),
    (r"can't|cannot", "cannot"),
    (r"won't", "will not"),
    (r"don't", "do not"),
    (r"doesn't", "does not"),
    (r"didn't", "did not"),
    (r"isn't", "is not"),
    (r"aren't", "are not"),
    (r"it's", "it is"),
    (r"we're", "we are"),
    (r"they're", "they are"),
    (r"i'm", "I am"),
    (r"'cause|cuz|coz", "because"),
    (r"gonna", "going to"),
    (r"wanna", "want to"),
    (r"kinda|sorta", "somewhat"),
    (r"a lot of|lots of", "many"),
    (r"stuff", "material"),
    (r"okay|ok", "acceptable"),
    (r"yeah|yep", "yes"),
    (r"asap", "as soon as possible"),
    (r"thanks", "thank you"),
)


def formal(text: str) -> str:
    out = " ".join(text.strip().strip("\"'“”").split())
    for pattern, repl in FORMAL:
        out = re.sub(rf"(?<![\w']){pattern}(?![\w'])", repl, out, flags=re.IGNORECASE)
    out = out.replace("!", ".").rstrip(" ,;")
    out = out[:1].upper() + out[1:]
    return out if out.endswith((".", "?")) else out + "."


def percent(p: str, n: str) -> str:
    value = (Decimal(p) * Decimal(n) / Decimal(100)).normalize()
    text = format(value, "f")
    return f"{p}% of {n} is {text}."


def policy(conv: Conversation) -> Reply:
    task = conv.task.strip()
    if match := REWRITE.search(task):
        return Reply(formal(match.group(1)))
    if match := PERCENT.search(task):
        return Reply(percent(match.group(1), match.group(2)))
    if match := SUMMARIZE.search(task):
        return Reply(f"Summary: {first_sentence(match.group(1).strip())}")
    return Reply(UNKNOWN_REPLY)
