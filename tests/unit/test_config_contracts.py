import tomllib

import pytest

from clearagent.config import load_project_config, tracing_config


def test_load_project_config_rejects_non_mapping_parser_result(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text("project = 'demo'\n", encoding="utf-8")
    monkeypatch.setattr("clearagent.config.tomllib.load", lambda handle: ["not", "a", "mapping"])

    with pytest.raises(ValueError, match="config .* must contain a mapping"):
        load_project_config(config_path)


def test_load_project_config_surfaces_malformed_toml(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text("[tracing\nenabled = true\n", encoding="utf-8")

    with pytest.raises(tomllib.TOMLDecodeError):
        load_project_config(config_path)


@pytest.mark.parametrize(
    ("config_text", "message"),
    [
        ("tracing = false\n", r"\[tracing\] must be a mapping"),
        ('[tracing]\nenabled = "yes"\n', "tracing.enabled must be a boolean"),
        ('[tracing]\ndb_path = ""\n', "tracing.db_path must be a non-empty string"),
        ("[tracing]\ndb_path = []\n", "tracing.db_path must be a non-empty string"),
    ],
)
def test_tracing_config_rejects_invalid_types_and_empty_paths(tmp_path, config_text, message):
    config_path = tmp_path / "config.toml"
    config_path.write_text(config_text, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        tracing_config(config_path)


def test_empty_tracing_table_uses_defaults(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text("[tracing]\n", encoding="utf-8")

    enabled, db_path = tracing_config(config_path)

    assert enabled is True
    assert db_path.as_posix() == ".clearagent/traces.sqlite"
