"""Stream reassembly: content, reasoning, tool-call fragments by index, usage."""

from __future__ import annotations

import json

from ratchet.proxy.stream import (
    ChatStreamAssembler,
    ResponsesStreamAssembler,
    SseParser,
    estimate_tokens,
)


def _sse(*payloads: object) -> bytes:
    lines = [f"data: {json.dumps(p)}\n\n" for p in payloads]
    return ("".join(lines) + "data: [DONE]\n\n").encode()


def _chunk(delta: dict[str, object], **extra: object) -> dict[str, object]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "m",
        "choices": [{"index": 0, "delta": delta, "finish_reason": extra.pop("finish", None)}],
        **extra,
    }


STREAM = _sse(
    _chunk({"role": "assistant", "reasoning_content": "Think"}),
    _chunk({"reasoning_content": "ing."}),
    _chunk({"content": "Hel"}),
    _chunk({"content": "lo"}),
    _chunk(
        {
            "tool_calls": [
                {
                    "index": 0,
                    "id": "call_a",
                    "type": "function",
                    "function": {"name": "github__close_issue", "arguments": '{"re'},
                }
            ]
        }
    ),
    _chunk({"tool_calls": [{"index": 1, "id": "call_b", "function": {"name": "gmail__send"}}]}),
    _chunk({"tool_calls": [{"index": 0, "function": {"arguments": 'po": "a/b"}'}}]}),
    _chunk({"tool_calls": [{"index": 1, "function": {"arguments": "{}"}}]}, finish="tool_calls"),
    {"id": "chatcmpl-1", "choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 7}},
)


def test_sse_parser_handles_events_split_across_arbitrary_chunks() -> None:
    parser = SseParser()
    events: list[str] = []
    for i in range(0, len(STREAM), 7):
        events.extend(parser.feed(STREAM[i : i + 7]))
    events.extend(parser.close())
    assert events[-1] == "[DONE]"
    assert len(events) == 10


def test_assembler_reassembles_content_reasoning_tool_calls_and_usage() -> None:
    asm = ChatStreamAssembler()
    for i in range(0, len(STREAM), 5):
        asm.feed(STREAM[i : i + 5])
    asm.close()
    out = asm.response()
    assert asm.done
    message = out["choices"][0]["message"]  # type: ignore[index]
    assert message["content"] == "Hello"
    assert message["reasoning_content"] == "Thinking."
    assert message["tool_calls"] == [
        {
            "id": "call_a",
            "type": "function",
            "function": {"name": "github__close_issue", "arguments": '{"repo": "a/b"}'},
        },
        {
            "id": "call_b",
            "type": "function",
            "function": {"name": "gmail__send", "arguments": "{}"},
        },
    ]
    assert out["choices"][0]["finish_reason"] == "tool_calls"  # type: ignore[index]
    assert out["usage"] == {"prompt_tokens": 11, "completion_tokens": 7}
    assert out["object"] == "chat.completion" and out["id"] == "chatcmpl-1"


def test_assembler_without_usage_leaves_usage_absent() -> None:
    asm = ChatStreamAssembler()
    asm.feed(_sse(_chunk({"content": "hi"})))
    assert "usage" not in asm.response()
    assert asm.text == "hi"


def test_assembler_accepts_reasoning_alias_and_crlf_lines() -> None:
    raw = _sse(_chunk({"reasoning": "r"}), _chunk({"content": "c"})).replace(b"\n", b"\r\n")
    asm = ChatStreamAssembler()
    asm.feed(raw)
    message = asm.response()["choices"][0]["message"]  # type: ignore[index]
    assert message == {"role": "assistant", "content": "c", "reasoning_content": "r"}


def test_assembler_ignores_comments_and_malformed_events() -> None:
    asm = ChatStreamAssembler()
    asm.feed(b": keep-alive\n\ndata: {not json\n\n" + _sse(_chunk({"content": "ok"})))
    assert asm.text == "ok"


def test_responses_assembler_takes_final_response_from_completed_event() -> None:
    final = {"id": "resp_1", "output": [], "usage": {"input_tokens": 3, "output_tokens": 2}}
    raw = (
        b'data: {"type": "response.output_text.delta", "delta": "Hi"}\n\n'
        + f"data: {json.dumps({'type': 'response.completed', 'response': final})}\n\n".encode()
    )
    asm = ResponsesStreamAssembler()
    asm.feed(raw)
    assert asm.response() == final


def test_responses_assembler_falls_back_to_text_deltas() -> None:
    asm = ResponsesStreamAssembler()
    asm.feed(b'data: {"type": "response.output_text.delta", "delta": "Hi"}\n\n')
    output = asm.response()["output"]
    assert output == [{"type": "message", "content": [{"type": "output_text", "text": "Hi"}]}]


def test_estimate_tokens_is_about_four_chars_per_token() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2
