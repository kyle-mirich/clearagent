from __future__ import annotations

import base64
import csv
import hashlib
from io import BytesIO
from io import StringIO
from pathlib import Path
import sys
import tarfile
import zipfile

import pytest

from scripts.check_distribution import (
    DistributionCheckError,
    ProjectMetadata,
    expected_package_inventory,
    find_distribution_artifacts,
    inspect_sdist,
    inspect_wheel,
    load_project_metadata,
    run_checked,
)


PROJECT = ProjectMetadata(
    name="clearagent",
    version="0.1.0",
    description="Test description.",
    requires_python=">=3.14",
    license_expression="MIT",
    classifiers=(
        "Development Status :: 3 - Alpha",
        "License :: OSI Approved :: MIT License",
    ),
    project_urls=(
        ("Homepage", "https://example.invalid/clearagent"),
        ("Documentation", "https://example.invalid/clearagent/docs"),
        ("Source", "https://example.invalid/clearagent/source"),
        ("Issues", "https://example.invalid/clearagent/issues"),
        ("Changelog", "https://example.invalid/clearagent/changelog"),
    ),
)
DIST_INFO = "clearagent-0.1.0.dist-info"
RECORD_MEMBER = f"{DIST_INFO}/RECORD"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


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
            "Classifier: Development Status :: 3 - Alpha",
            "Classifier: License :: OSI Approved :: MIT License",
            "Project-URL: Homepage, https://example.invalid/clearagent",
            "Project-URL: Documentation, https://example.invalid/clearagent/docs",
            "Project-URL: Source, https://example.invalid/clearagent/source",
            "Project-URL: Issues, https://example.invalid/clearagent/issues",
            "Project-URL: Changelog, https://example.invalid/clearagent/changelog",
            "",
            "# ClearAgent",
        ]
    )


def _wheel_members(*, entry_point: str = "clearagent.cli:app") -> dict[str, bytes]:
    members = {
        relative_name: (REPOSITORY_ROOT / "src" / relative_name).read_bytes()
        for relative_name in expected_package_inventory(REPOSITORY_ROOT)
    }
    members.update(
        {
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
        }
    )
    return members


def _record_rows(members: dict[str, bytes]) -> list[list[str]]:
    rows = []
    for name, content in sorted(members.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=")
        rows.append([name, f"sha256={digest.decode('ascii')}", str(len(content))])
    rows.append([RECORD_MEMBER, "", ""])
    return rows


def _encode_record(rows: list[list[str]]) -> bytes:
    output = StringIO()
    csv.writer(output, lineterminator="\n").writerows(rows)
    return output.getvalue().encode()


def _write_wheel(
    path: Path,
    members: dict[str, bytes],
    *,
    record: bytes | None = None,
) -> None:
    archived_members = dict(members)
    archived_members[RECORD_MEMBER] = (
        _encode_record(_record_rows(members)) if record is None else record
    )
    with zipfile.ZipFile(path, mode="w") as wheel:
        for name, content in archived_members.items():
            wheel.writestr(name, content)


def _write_sdist(path: Path, *, omit: str | None = None) -> None:
    root = "clearagent-0.1.0"
    members = {
        "LICENSE": b"MIT License\n",
        "README.md": b"# ClearAgent\n",
        "pyproject.toml": (b'[project]\nname = "clearagent"\nversion = "0.1.0"\n'),
    }
    members.update(
        {
            f"src/{relative_name}": (REPOSITORY_ROOT / "src" / relative_name).read_bytes()
            for relative_name in expected_package_inventory(REPOSITORY_ROOT)
        }
    )
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


@pytest.mark.parametrize(
    ("classifiers", "urls", "message"),
    [
        (["Programming Language :: Python :: 3"], True, "Development Status :: 3 - Alpha"),
        (["Development Status :: 3 - Alpha"], False, "project.urls"),
    ],
)
def test_project_metadata_requires_alpha_status_and_release_urls(
    tmp_path: Path,
    classifiers: list[str],
    urls: bool,
    message: str,
) -> None:
    url_block = ""
    if urls:
        url_block = """
[project.urls]
Homepage = "https://example.invalid"
Documentation = "https://example.invalid/docs"
Source = "https://example.invalid/source"
Issues = "https://example.invalid/issues"
Changelog = "https://example.invalid/changelog"
"""
    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        'name = "clearagent"\n'
        'version = "0.1.0"\n'
        'description = "Test description."\n'
        'requires-python = ">=3.14"\n'
        'license = "MIT"\n'
        f"classifiers = {classifiers!r}\n"
        f"{url_block}",
        encoding="utf-8",
    )

    with pytest.raises(DistributionCheckError, match=message):
        load_project_metadata(tmp_path)

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


@pytest.mark.parametrize(
    "omitted_member",
    ["clearagent/graph/graph.py", "clearagent/evals/generate.py"],
)
def test_inspect_wheel_rejects_omitted_public_module(
    tmp_path: Path,
    omitted_member: str,
) -> None:
    wheel = tmp_path / "clearagent-0.1.0-py3-none-any.whl"
    members = _wheel_members()
    members.pop(omitted_member)
    _write_wheel(wheel, members)

    with pytest.raises(DistributionCheckError, match=omitted_member):
        inspect_wheel(wheel, PROJECT)


def test_inspect_wheel_rejects_empty_record(tmp_path: Path) -> None:
    wheel = tmp_path / "clearagent-0.1.0-py3-none-any.whl"
    _write_wheel(wheel, _wheel_members(), record=b"")

    with pytest.raises(DistributionCheckError, match="RECORD member coverage is incomplete"):
        inspect_wheel(wheel, PROJECT)


def test_inspect_wheel_rejects_record_missing_member_row(tmp_path: Path) -> None:
    wheel = tmp_path / "clearagent-0.1.0-py3-none-any.whl"
    members = _wheel_members()
    rows = [row for row in _record_rows(members) if row[0] != "clearagent/graph/graph.py"]
    _write_wheel(wheel, members, record=_encode_record(rows))

    with pytest.raises(DistributionCheckError, match="clearagent/graph/graph.py"):
        inspect_wheel(wheel, PROJECT)


def test_inspect_wheel_rejects_blank_record_integrity_for_non_record_member(
    tmp_path: Path,
) -> None:
    wheel = tmp_path / "clearagent-0.1.0-py3-none-any.whl"
    members = _wheel_members()
    rows = _record_rows(members)
    rows[0][1:] = ["", ""]
    _write_wheel(wheel, members, record=_encode_record(rows))

    with pytest.raises(DistributionCheckError, match="may be blank only"):
        inspect_wheel(wheel, PROJECT)


def test_inspect_wheel_rejects_wrong_record_hash(tmp_path: Path) -> None:
    wheel = tmp_path / "clearagent-0.1.0-py3-none-any.whl"
    members = _wheel_members()
    rows = _record_rows(members)
    rows[0][1] = "sha256=wrong"
    _write_wheel(wheel, members, record=_encode_record(rows))

    with pytest.raises(DistributionCheckError, match="incorrect hash"):
        inspect_wheel(wheel, PROJECT)


def test_inspect_wheel_rejects_wrong_record_size(tmp_path: Path) -> None:
    wheel = tmp_path / "clearagent-0.1.0-py3-none-any.whl"
    members = _wheel_members()
    rows = _record_rows(members)
    rows[0][2] = str(int(rows[0][2]) + 1)
    _write_wheel(wheel, members, record=_encode_record(rows))

    with pytest.raises(DistributionCheckError, match="incorrect size"):
        inspect_wheel(wheel, PROJECT)


def test_inspect_wheel_rejects_wrong_console_entry_point(tmp_path: Path) -> None:
    wheel = tmp_path / "clearagent-0.1.0-py3-none-any.whl"
    _write_wheel(wheel, _wheel_members(entry_point="clearagent.missing:app"))

    with pytest.raises(DistributionCheckError, match="clearagent.missing:app"):
        inspect_wheel(wheel, PROJECT)


@pytest.mark.parametrize(
    ("metadata_line", "message"),
    [
        ("Classifier: Development Status :: 3 - Alpha\n", "incorrect classifiers"),
        (
            "Project-URL: Changelog, https://example.invalid/clearagent/changelog\n",
            "incorrect project URLs",
        ),
    ],
)
def test_inspect_wheel_rejects_missing_release_metadata(
    tmp_path: Path,
    metadata_line: str,
    message: str,
) -> None:
    wheel = tmp_path / "clearagent-0.1.0-py3-none-any.whl"
    members = _wheel_members()
    metadata_member = f"{DIST_INFO}/METADATA"
    members[metadata_member] = members[metadata_member].replace(metadata_line.encode(), b"")
    _write_wheel(wheel, members)

    with pytest.raises(DistributionCheckError, match=message):
        inspect_wheel(wheel, PROJECT)


def test_inspect_sdist_rejects_missing_required_package_file(tmp_path: Path) -> None:
    sdist = tmp_path / "clearagent-0.1.0.tar.gz"
    _write_sdist(sdist, omit="src/clearagent/chat/static/styles.css")

    with pytest.raises(DistributionCheckError, match="static/styles.css"):
        inspect_sdist(sdist, PROJECT)


@pytest.mark.parametrize(
    "omitted_member",
    ["src/clearagent/graph/graph.py", "src/clearagent/evals/generate.py"],
)
def test_inspect_sdist_rejects_omitted_public_module(
    tmp_path: Path,
    omitted_member: str,
) -> None:
    sdist = tmp_path / "clearagent-0.1.0.tar.gz"
    _write_sdist(sdist, omit=omitted_member)

    with pytest.raises(DistributionCheckError, match=omitted_member):
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


def test_distribution_subprocesses_are_forced_offline(tmp_path: Path) -> None:
    result = run_checked(
        [sys.executable, "-c", "import os; print(os.environ.get('UV_OFFLINE'))"],
        cwd=tmp_path,
        description="inspect offline environment",
    )

    assert result.stdout.strip() == "1"
