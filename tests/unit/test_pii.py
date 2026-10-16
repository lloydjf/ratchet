"""PII tokenizer — no raw emails/phones/keys survive; the token map round-trips."""

from __future__ import annotations

import re
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest

from ratchet.config.settings import Settings, configure, reset_settings
from ratchet.errors import ConfigError
from ratchet.pii import MAP_FILENAME, SECRET_TOKEN, PiiTokenizer, find_secrets

CORPUS = Path(__file__).resolve().parents[1] / "fixtures" / "pii_corpus.txt"

EMAILS = [
    "jane.doe@example.com",
    "ops-team+alerts@widgets.example.org",
    "Jane.Doe@example.com",
    "billing@acme-widgets.example",
]
PHONES = ["(415) 555-0134", "415-555-0199", "+44 20 7946 0958", "+49 30 901820", "416.555.0172"]
SECRETS = [
    "sk-test-4f9aB2cD8eF1gH3iJ5kL7mN9oP0qRsTuV",
    "tvly-dev-AbCdEf1234567890GhIjKlMn",
    "ghp_FAKE0000111122223333444455556666777788",
    "AKIAFAKEFAKEFAKE1234",
    "wJalrXUtnFEMI/K7MDENG/bPxRfiCYFAKEKEY99",
    "abcDEF123456ghiJKL789012mnoPQR345678",
    "hunter2-but-longer",
    "0123456789abcdef0123456789abcdef",
    "Zm9vYmFyQmF6UXV4MTIzNDU2Nzg5MEFCQ0RFRkdISUpLTE1OT1BRUlNU",
]
KEPT = ["$1,234.56", "2026-10-14 17:00 UTC", "order 9921", "issue #482", "rate 1.08"]
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


@pytest.fixture(autouse=True)
def _settings(tmp_path: Path) -> Iterator[None]:
    configure(Settings(_env_file=None, ratchet_data_dir=tmp_path / "data"))
    yield
    reset_settings()


def _tokenizer(tmp_path: Path) -> PiiTokenizer:
    return PiiTokenizer(tmp_path / "data" / MAP_FILENAME, tmp_path / "keys" / "pii.key")


def test_corpus_has_no_raw_emails_phones_or_keys_after_tokenizing(tmp_path: Path) -> None:
    out = _tokenizer(tmp_path).tokenize_text(CORPUS.read_text(encoding="utf-8"))
    for raw in [*EMAILS, *PHONES, *SECRETS]:
        assert raw not in out, raw
    assert not EMAIL_RE.search(out)
    assert "sk-" not in out and "tvly-" not in out
    for kept in KEPT:
        assert kept in out, kept
    assert "3f2a9c1b7d4e5f60718293a4b5c6d7e8f9012345" in out  # a commit sha is not a secret


def test_same_value_gets_same_token_and_distinct_values_distinct_tokens(tmp_path: Path) -> None:
    tok = _tokenizer(tmp_path)
    first = tok.tokenize_text("mail jane.doe@example.com or bob@example.com")
    again = tok.tokenize_text("cc jane.doe@example.com, call 415-555-0199")
    assert first == "mail <EMAIL_1> or <EMAIL_2>"
    assert again == "cc <EMAIL_1>, call <PHONE_1>"


def test_token_map_round_trips_through_encrypted_file(tmp_path: Path) -> None:
    text = CORPUS.read_text(encoding="utf-8")
    tok = _tokenizer(tmp_path)
    tokenized = tok.tokenize_text(text)
    tok.save()
    map_path = tmp_path / "data" / MAP_FILENAME
    blob = map_path.read_bytes()
    assert b"jane.doe" not in blob and b"555" not in blob

    fresh = _tokenizer(tmp_path)
    restored = fresh.detokenize(tokenized)
    assert isinstance(restored, str)
    for raw in [*EMAILS, *PHONES]:
        assert raw in restored
    for raw in SECRETS:
        assert raw not in restored  # secrets are never kept, not even encrypted
    assert fresh.tokenize_text("jane.doe@example.com") == "<EMAIL_1>"


def test_tokenize_obj_walks_nested_json(tmp_path: Path) -> None:
    tok = _tokenizer(tmp_path)
    value = {"to": ["jane.doe@example.com"], "meta": {"phone": "(415) 555-0134", "n": 3}}
    out = tok.tokenize_obj(value)
    assert out == {"to": ["<EMAIL_1>"], "meta": {"phone": "<PHONE_1>", "n": 3}}
    assert tok.detokenize(out) == value


def test_key_file_is_created_with_owner_only_permissions(tmp_path: Path) -> None:
    tok = _tokenizer(tmp_path)
    tok.tokenize_text("x@example.com")
    tok.save()
    mode = stat.S_IMODE((tmp_path / "keys" / "pii.key").stat().st_mode)
    assert mode == 0o600


def test_wrong_key_refuses_to_decrypt_map(tmp_path: Path) -> None:
    tok = _tokenizer(tmp_path)
    tok.tokenize_text("x@example.com")
    tok.save()
    other = PiiTokenizer(tmp_path / "data" / MAP_FILENAME, tmp_path / "other.key")
    with pytest.raises(ConfigError):
        other.tokenize_text("y@example.com")


def test_for_settings_uses_data_dir_map_and_settings_key_path(tmp_path: Path) -> None:
    tok = PiiTokenizer.for_settings()
    assert tok.map_path == tmp_path / "data" / MAP_FILENAME
    assert tok.key_path == tmp_path / "pii.key"


def test_find_secrets_keeps_labels_and_redacts_values() -> None:
    assert find_secrets("api_key=abcdefgh1234") == f"api_key={SECRET_TOKEN}"
    assert find_secrets("plain words stay") == "plain words stay"
