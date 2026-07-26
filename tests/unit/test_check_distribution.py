from __future__ import annotations

from io import BytesIO
from pathlib import Path
import sys
import tarfile
import zipfile

import pytest

from scripts.check_distribution import (
    DistributionCheckError,
    ProjectMetadata,
    find_distribution_artifacts,
    inspect_sdist,
    inspect_wheel,
    run_checked,
)


PROJECT = ProjectMetadata(
    name="clearagent",
    version="0.1.0",
    description="Test description.",
    requires_python=">=3.14",
    license_expression="MIT",
)
DIST_INFO = "clearagent-0.1.0.dist-info"


def _metadata() -> str:
    return "\n".join(
        [
            "Metadata-Version: 2.4",
            "Name: clearagent",
            "Version: 0.1.0",
            "Summary: Test description.",
            "Requires-Python: >=3.14",
            "License-Expression: MIT",
            "Description-Content-Type: text/markdown",
            "",
            "# ClearAgent",
        ]
    )


def _wheel_members(*, entry_point: str = "clearagent.cli:app") -> dict[str, bytes]:
    members = {
        "clearagent/__init__.py": b"from clearagent.create import create_agent\n",
        "clearagent/agent.py": b"class Agent: ...\n",
        "clearagent/cli.py": b"app = object()\n",
        "clearagent/chat/app.py": b"def create_chat_app(): ...\n",
        "clearagent/chat/static/index.html": b"<title>ClearAgent</title>\n",
        "clearagent/chat/static/styles.css": b"body {}\n",
        "clearagent/chat/static/app.js": b"console.log('ClearAgent');\n",
        f"{DIST_INFO}/METADATA": _metadata().encode(),
        f"{DIST_INFO}/WHEEL": (
            "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ).encode(),
        f"{DIST_INFO}/entry_points.txt": (
            "[console_scripts]\n"
            f"clearagent = {entry_point}\n\n"
            "[pytest11]\n"
            "clearagent = clearagent.pytest_plugin.plugin\n"
        ).encode(),
        f"{DIST_INFO}/licenses/LICENSE": b"MIT License\n",
        f"{DIST_INFO}/RECORD": b"record\n",
    }
    return members


def _write_wheel(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, mode="w") as wheel:
        for name, content in members.items():
            wheel.writestr(name, content)


def _write_sdist(path: Path, *, omit: str | None = None) -> None:
    root = "clearagent-0.1.0"
    members = {
        "LICENSE": b"MIT License\n",
        "README.md": b"# ClearAgent\n",
        "pyproject.toml": (
            b"[project]\nname = \"clearagent\"\nversion = \"0.1.0\"\n"
        ),
        "src/clearagent/__init__.py": b"__all__ = []\n",
        "src/clearagent/agent.py": b"class Agent: ...\n",
        "src/clearagent/cli.py": b"app = object()\n",
        "src/clearagent/chat/app.py": b"def create_chat_app(): ...\n",
        "src/clearagent/chat/static/index.html": b"<title>ClearAgent</title>\n",
        "src/clearagent/chat/static/styles.css": b"body {}\n",
        "src/clearagent/chat/static/app.js": b"console.log('ClearAgent');\n",
    }
    if omit is not None:
        members.pop(omit)

    with tarfile.open(path, mode="w:gz") as sdist:
        for relative_name, content in members.items():
            info = tarfile.TarInfo(f"{root}/{relative_name}")
            info.size = len(content)
            sdist.addfile(info, BytesIO(content))


def test_find_distribution_artifacts_requires_exact_pair(tmp_path: Path) -> None:
    (tmp_path / "clearagent-0.1.0-py3-none-any.whl").touch()
    (tmp_path / ".gitignore").touch()

    with pytest.raises(DistributionCheckError, match="exactly one wheel and one"):
        find_distribution_artifacts(tmp_path)

    (tmp_path / "clearagent-0.1.0.tar.gz").touch()
    (tmp_path / "stale.txt").touch()
    with pytest.raises(DistributionCheckError, match="stale.txt"):
        find_distribution_artifacts(tmp_path)


def test_inspect_wheel_accepts_required_package_metadata_and_assets(tmp_path: Path) -> None:
    wheel = tmp_path / "clearagent-0.1.0-py3-none-any.whl"
    _write_wheel(wheel, _wheel_members())

    inspect_wheel(wheel, PROJECT)


def test_inspect_wheel_rejects_missing_static_asset(tmp_path: Path) -> None:
    wheel = tmp_path / "clearagent-0.1.0-py3-none-any.whl"
    members = _wheel_members()
    members.pop("clearagent/chat/static/app.js")
    _write_wheel(wheel, members)

    with pytest.raises(DistributionCheckError, match="static/app.js"):
        inspect_wheel(wheel, PROJECT)


def test_inspect_wheel_rejects_wrong_console_entry_point(tmp_path: Path) -> None:
    wheel = tmp_path / "clearagent-0.1.0-py3-none-any.whl"
    _write_wheel(wheel, _wheel_members(entry_point="clearagent.missing:app"))

    with pytest.raises(DistributionCheckError, match="clearagent.missing:app"):
        inspect_wheel(wheel, PROJECT)


def test_inspect_sdist_rejects_missing_required_package_file(tmp_path: Path) -> None:
    sdist = tmp_path / "clearagent-0.1.0.tar.gz"
    _write_sdist(sdist, omit="src/clearagent/chat/static/styles.css")

    with pytest.raises(DistributionCheckError, match="static/styles.css"):
        inspect_sdist(sdist, PROJECT)


def test_run_checked_reports_command_output(tmp_path: Path) -> None:
    with pytest.raises(DistributionCheckError) as error:
        run_checked(
            [
                sys.executable,
                "-c",
                "import sys; print('useful stdout'); print('useful stderr', file=sys.stderr); raise SystemExit(7)",
            ],
            cwd=tmp_path,
            description="intentional failure",
        )

    message = str(error.value)
    assert "intentional failure failed with exit code 7" in message
    assert "useful stdout" in message
    assert "useful stderr" in message
