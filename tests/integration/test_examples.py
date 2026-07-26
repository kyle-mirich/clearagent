import runpy
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_customer_support_example_runs_as_script(capsys, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    runpy.run_path(
        str(PROJECT_ROOT / "examples/customer_support/agent.py"),
        run_name="__main__",
    )

    output = capsys.readouterr().out

    assert "Order A123 has shipped" in output
    assert (tmp_path / ".clearagent/traces.sqlite").is_file()


def test_multinode_example_runs_as_script(capsys, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    runpy.run_path(
        str(PROJECT_ROOT / "examples/multinode/flow.py"),
        run_name="__main__",
    )

    output = capsys.readouterr().out

    assert "Here is the final response." in output
    assert (tmp_path / ".clearagent/traces.sqlite").is_file()
