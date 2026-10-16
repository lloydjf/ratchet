"""``.env.example`` covers every ``Settings`` field and is what the generator writes."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

from ratchet.config.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = ROOT / ".env.example"
_ASSIGN = re.compile(r"^(?:# )?(?P<key>[A-Z][A-Z0-9_]*)=", re.M)


def _generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "gen_env_example", ROOT / "scripts" / "gen_env_example.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _keys() -> list[str]:
    return _ASSIGN.findall(ENV_EXAMPLE.read_text(encoding="utf-8"))


def test_every_settings_field_has_a_line_in_env_example() -> None:
    keys = _keys()
    missing = sorted(name.upper() for name in Settings.model_fields if name.upper() not in keys)
    assert missing == [], f"add to .env.example (scripts/gen_env_example.py): {missing}"


def test_env_example_lists_only_settings_fields_once() -> None:
    keys = _keys()
    fields = {name.upper() for name in Settings.model_fields}
    assert sorted(set(keys) - fields) == []
    assert len(keys) == len(set(keys))


def test_every_field_has_a_description_comment() -> None:
    lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if _ASSIGN.match(line) and not line.startswith("# One of"):
            above = lines[index - 1]
            assert above.startswith("# ") and not _ASSIGN.match(above), line


def test_env_example_is_regenerated_from_the_model() -> None:
    gen = _generator()
    assert gen.render() == ENV_EXAMPLE.read_text(encoding="utf-8"), (
        "run `uv run python scripts/gen_env_example.py`"
    )


def test_env_example_loads_as_settings_with_simulate_on_and_no_secrets() -> None:
    settings = Settings(_env_file=ENV_EXAMPLE)  # type: ignore[call-arg]
    assert settings.ratchet_neighborhood_threshold is None or isinstance(
        settings.ratchet_neighborhood_threshold, float
    )
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "RATCHET_SIMULATE_ENABLED=true" in text.splitlines()
    for name, field in Settings.model_fields.items():
        if "SecretStr" in str(field.annotation) and name != "ratchet_action_token_secret":
            assert f"{name.upper()}=" in text.splitlines(), name


def test_existing_values_win_over_defaults(tmp_path: Path) -> None:
    gen = _generator()
    custom = tmp_path / ".env.example"
    custom.write_text("RATCHET_BATCH_SIZE=7\nRATCHET_DEBUG=\n", encoding="utf-8")
    text = gen.render(gen.existing_values(custom))
    lines = text.splitlines()
    assert "RATCHET_BATCH_SIZE=7" in lines
    assert "RATCHET_DEBUG=false" in lines  # an empty value falls back to the default
    assert "NEBIUS_TOKEN_FACTORY_KEY=" in lines
