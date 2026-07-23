from pathlib import Path

from clearagent.config import load_project_config, tracing_config


def test_missing_config_uses_runtime_defaults(tmp_path):
    enabled, db_path = tracing_config(tmp_path / "missing.toml")

    assert enabled is True
    assert db_path.as_posix() == ".clearagent/traces.sqlite"


def test_tracing_config_reads_generated_settings(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[tracing]\nenabled = false\ndb_path = "var/custom.sqlite"\n',
        encoding="utf-8",
    )

    assert load_project_config(path)["tracing"]["enabled"] is False
    enabled, db_path = tracing_config(path)
    assert enabled is False
    assert db_path == Path("var/custom.sqlite")
