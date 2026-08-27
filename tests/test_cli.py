from typer.testing import CliRunner

from clearagent.command import app

runner = CliRunner()


def test_eval_deterministic_scores_an_instruction():
    result = runner.invoke(
        app,
        [
            "eval",
            "Build a release notes summarizer for changelog entries.",
            "--instruction",
            "Summarize changelog entries into added, changed, and fixed bullets.",
            "--deterministic",
            "--cases",
            "2",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Score" in result.output


def test_build_deterministic_runs_the_full_loop(tmp_path):
    result = runner.invoke(
        app,
        [
            "build",
            "Build a release notes summarizer for changelog entries.",
            "--deterministic",
            "--level",
            "quick",
            "--export",
            str(tmp_path / "prompt.md"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Winner" in result.output
    assert (tmp_path / "prompt.md").exists()
