"""Build and verify ClearAgent exactly as an external consumer installs it."""

from __future__ import annotations

import base64
import configparser
import csv
from dataclasses import dataclass
from email.parser import Parser
import hashlib
from io import StringIO
import os
from pathlib import Path
from pathlib import PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from typing import NoReturn
import zipfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INSTALLED_SMOKE_SCRIPT = PROJECT_ROOT / "scripts" / "installed_distribution_smoke.py"
IGNORED_SOURCE_DIRECTORIES = {"__pycache__", ".mypy_cache", ".pytest_cache"}
IGNORED_SOURCE_SUFFIXES = {".pyc", ".pyo"}


class DistributionCheckError(RuntimeError):
    """Raised when a built distribution violates a release invariant."""


@dataclass(frozen=True)
class ProjectMetadata:
    name: str
    version: str
    description: str
    requires_python: str
    license_expression: str
    classifiers: tuple[str, ...]
    project_urls: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class DistributionArtifacts:
    sdist: Path
    wheel: Path


def _fail(message: str) -> NoReturn:
    raise DistributionCheckError(message)


def load_project_metadata(project_root: Path = PROJECT_ROOT) -> ProjectMetadata:
    pyproject_path = project_root / "pyproject.toml"
    with pyproject_path.open("rb") as pyproject_file:
        project = tomllib.load(pyproject_file)["project"]

    license_expression = project["license"]
    if not isinstance(license_expression, str):
        _fail("project.license must be an SPDX string for distribution verification")
    classifiers = tuple(project.get("classifiers", ()))
    if "Development Status :: 3 - Alpha" not in classifiers:
        _fail("project.classifiers must declare Development Status :: 3 - Alpha")
    project_urls = project.get("urls")
    if not isinstance(project_urls, dict):
        _fail("project.urls must be a mapping for distribution verification")
    required_urls = {"Homepage", "Documentation", "Source", "Issues", "Changelog"}
    missing_urls = sorted(required_urls - set(project_urls))
    invalid_urls = sorted(
        label
        for label, url in project_urls.items()
        if not isinstance(url, str) or not url.startswith("https://")
    )
    if missing_urls or invalid_urls:
        _fail(
            "project.urls must contain HTTPS release links: "
            f"missing={missing_urls!r} invalid={invalid_urls!r}"
        )

    return ProjectMetadata(
        name=project["name"],
        version=project["version"],
        description=project["description"],
        requires_python=project["requires-python"],
        license_expression=license_expression,
        classifiers=classifiers,
        project_urls=tuple(project_urls.items()),
    )


def find_distribution_artifacts(dist_directory: Path) -> DistributionArtifacts:
    files = sorted(
        path for path in dist_directory.iterdir() if path.is_file() and path.name != ".gitignore"
    )
    wheels = [path for path in files if path.suffix == ".whl"]
    sdists = [path for path in files if path.name.endswith(".tar.gz")]
    expected = {*wheels, *sdists}
    unexpected = [path.name for path in files if path not in expected]

    if len(wheels) != 1 or len(sdists) != 1 or unexpected:
        _fail(
            "distribution output must contain exactly one wheel and one .tar.gz sdist; "
            f"wheels={[path.name for path in wheels]!r} "
            f"sdists={[path.name for path in sdists]!r} unexpected={unexpected!r}"
        )
    return DistributionArtifacts(sdist=sdists[0], wheel=wheels[0])


def _missing_members(actual: set[str], required: set[str], artifact: Path) -> None:
    missing = sorted(required - actual)
    if missing:
        _fail(f"{artifact.name} is missing required members: {missing!r}")


def expected_package_inventory(project_root: Path = PROJECT_ROOT) -> set[str]:
    """Return every source package file that a distribution must contain."""
    source_directory = project_root / "src" / "clearagent"
    if not source_directory.is_dir():
        _fail(f"source package directory does not exist: {source_directory}")

    inventory = {
        path.relative_to(project_root / "src").as_posix()
        for path in source_directory.rglob("*")
        if path.is_file()
        and not any(part in IGNORED_SOURCE_DIRECTORIES for part in path.parts)
        and path.suffix not in IGNORED_SOURCE_SUFFIXES
    }
    if not inventory:
        _fail(f"source package directory contains no package files: {source_directory}")
    return inventory


def _require_normalized_members(members: list[str], artifact: Path) -> None:
    duplicates = sorted({member for member in members if members.count(member) > 1})
    if duplicates:
        _fail(f"{artifact.name} contains duplicate members: {duplicates!r}")

    invalid = []
    for member in members:
        normalized = PurePosixPath(member)
        if (
            not member
            or member.startswith("/")
            or "\\" in member
            or normalized.as_posix() != member
            or any(part in {".", ".."} for part in normalized.parts)
        ):
            invalid.append(member)
    if invalid:
        _fail(f"{artifact.name} contains non-normalized members: {sorted(invalid)!r}")


def _require_exact_inventory(
    actual: set[str],
    expected: set[str],
    artifact: Path,
) -> None:
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing or unexpected:
        _fail(
            f"{artifact.name} package inventory does not match src/clearagent: "
            f"missing={missing!r} unexpected={unexpected!r}"
        )


def _require_nonempty_zip_members(
    archive: zipfile.ZipFile,
    members: set[str],
    artifact: Path,
) -> None:
    empty = sorted(member for member in members if archive.getinfo(member).file_size == 0)
    if empty:
        _fail(f"{artifact.name} contains empty required members: {empty!r}")


def _record_hash(content: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=")
    return f"sha256={digest.decode('ascii')}"


def _validate_wheel_record(
    archive: zipfile.ZipFile,
    members: set[str],
    record_member: str,
    artifact: Path,
) -> None:
    try:
        record_text = archive.read(record_member).decode("utf-8")
    except UnicodeDecodeError as exc:
        _fail(f"{artifact.name} RECORD is not valid UTF-8: {exc}")

    rows = list(csv.reader(StringIO(record_text)))
    malformed_rows = [row for row in rows if len(row) != 3 or not row[0]]
    if malformed_rows:
        _fail(f"{artifact.name} RECORD contains malformed rows: {malformed_rows!r}")

    record_paths = [row[0] for row in rows]
    _require_normalized_members(record_paths, artifact)
    record_path_set = set(record_paths)
    missing_rows = sorted(members - record_path_set)
    unexpected_rows = sorted(record_path_set - members)
    if missing_rows or unexpected_rows:
        _fail(
            f"{artifact.name} RECORD member coverage is incomplete: "
            f"missing={missing_rows!r} unexpected={unexpected_rows!r}"
        )

    for member, recorded_hash, recorded_size in rows:
        if member == record_member:
            if recorded_hash or recorded_size:
                _fail(f"{artifact.name} RECORD row must have blank hash and size")
            continue
        if not recorded_hash or not recorded_size:
            _fail(
                f"{artifact.name} RECORD hash and size may be blank only for "
                f"{record_member!r}; member={member!r}"
            )

        content = archive.read(member)
        expected_hash = _record_hash(content)
        if recorded_hash != expected_hash:
            _fail(
                f"{artifact.name} RECORD has incorrect hash for {member!r}: "
                f"expected={expected_hash!r} actual={recorded_hash!r}"
            )
        try:
            size = int(recorded_size)
        except ValueError:
            _fail(f"{artifact.name} RECORD has invalid size for {member!r}: {recorded_size!r}")
        if size != len(content):
            _fail(
                f"{artifact.name} RECORD has incorrect size for {member!r}: "
                f"expected={len(content)} actual={size}"
            )


def inspect_wheel(
    wheel_path: Path,
    project: ProjectMetadata,
    *,
    project_root: Path = PROJECT_ROOT,
) -> None:
    with zipfile.ZipFile(wheel_path) as wheel:
        file_members = [info.filename for info in wheel.infolist() if not info.is_dir()]
        _require_normalized_members(file_members, wheel_path)
        members = set(file_members)
        metadata_members = sorted(
            member for member in members if member.endswith(".dist-info/METADATA")
        )
        if len(metadata_members) != 1:
            _fail(
                f"{wheel_path.name} must contain exactly one .dist-info/METADATA file; "
                f"found={metadata_members!r}"
            )

        dist_info = metadata_members[0].removesuffix("METADATA")
        required = {
            f"{dist_info}METADATA",
            f"{dist_info}WHEEL",
            f"{dist_info}RECORD",
            f"{dist_info}entry_points.txt",
            f"{dist_info}licenses/LICENSE",
        }
        _missing_members(members, required, wheel_path)
        _require_nonempty_zip_members(wheel, required - {f"{dist_info}RECORD"}, wheel_path)
        expected_package = expected_package_inventory(project_root)
        actual_package = {member for member in members if member.startswith("clearagent/")}
        _require_exact_inventory(actual_package, expected_package, wheel_path)
        _validate_wheel_record(wheel, members, f"{dist_info}RECORD", wheel_path)

        metadata_text = wheel.read(f"{dist_info}METADATA").decode("utf-8")
        metadata = Parser().parsestr(metadata_text)
        expected_metadata = {
            "Name": project.name,
            "Version": project.version,
            "Summary": project.description,
            "Requires-Python": project.requires_python,
            "License-Expression": project.license_expression,
            "Description-Content-Type": "text/markdown",
        }
        mismatches = {
            key: {"expected": expected, "actual": metadata.get(key)}
            for key, expected in expected_metadata.items()
            if metadata.get(key) != expected
        }
        if mismatches:
            _fail(f"{wheel_path.name} has incorrect core metadata: {mismatches!r}")

        actual_classifiers = tuple(metadata.get_all("Classifier", []))
        if sorted(actual_classifiers) != sorted(project.classifiers):
            _fail(
                f"{wheel_path.name} has incorrect classifiers: "
                f"expected={project.classifiers!r} actual={actual_classifiers!r}"
            )
        actual_urls: list[tuple[str, str]] = []
        for value in metadata.get_all("Project-URL", []):
            if ", " not in value:
                _fail(f"{wheel_path.name} has malformed Project-URL metadata: {value!r}")
            label, url = value.split(", ", 1)
            actual_urls.append((label, url))
        if len(set(actual_urls)) != len(actual_urls) or sorted(actual_urls) != sorted(
            project.project_urls
        ):
            _fail(
                f"{wheel_path.name} has incorrect project URLs: "
                f"expected={project.project_urls!r} actual={tuple(actual_urls)!r}"
            )

        wheel_metadata = Parser().parsestr(wheel.read(f"{dist_info}WHEEL").decode("utf-8"))
        if wheel_metadata.get("Root-Is-Purelib") != "true":
            _fail(f"{wheel_path.name} must be a pure-Python wheel")
        if "py3-none-any" not in wheel_metadata.get_all("Tag", []):
            _fail(f"{wheel_path.name} must declare the py3-none-any compatibility tag")

        entry_points = configparser.ConfigParser(interpolation=None)
        entry_points.read_string(wheel.read(f"{dist_info}entry_points.txt").decode("utf-8"))
        expected_entry_points = {
            ("console_scripts", "clearagent"): "clearagent.cli:app",
            ("pytest11", "clearagent"): "clearagent.pytest_plugin.plugin",
        }
        bad_entry_points: dict[str, dict[str, str | None]] = {}
        for (section, name), expected in expected_entry_points.items():
            actual = entry_points.get(section, name, fallback=None)
            if actual != expected:
                bad_entry_points[f"{section}.{name}"] = {
                    "expected": expected,
                    "actual": actual,
                }
        if bad_entry_points:
            _fail(f"{wheel_path.name} has incorrect entry points: {bad_entry_points!r}")


def inspect_sdist(
    sdist_path: Path,
    project: ProjectMetadata,
    *,
    project_root: Path = PROJECT_ROOT,
) -> None:
    expected_root = f"{project.name}-{project.version}"
    required_relative = {
        "LICENSE",
        "README.md",
        "pyproject.toml",
    }
    required = {f"{expected_root}/{member}" for member in required_relative}

    with tarfile.open(sdist_path, mode="r:gz") as sdist:
        regular_file_names = [member.name for member in sdist.getmembers() if member.isfile()]
        _require_normalized_members(regular_file_names, sdist_path)
        regular_files = set(regular_file_names)
        _missing_members(regular_files, required, sdist_path)
        empty = sorted(
            member_name for member_name in required if sdist.getmember(member_name).size == 0
        )
        if empty:
            _fail(f"{sdist_path.name} contains empty required members: {empty!r}")

        expected_package = {
            f"{expected_root}/src/{member}" for member in expected_package_inventory(project_root)
        }
        package_prefix = f"{expected_root}/src/clearagent/"
        actual_package = {member for member in regular_files if member.startswith(package_prefix)}
        _require_exact_inventory(actual_package, expected_package, sdist_path)

        packaged_pyproject = sdist.extractfile(f"{expected_root}/pyproject.toml")
        if packaged_pyproject is None:
            _fail(f"{sdist_path.name} pyproject.toml could not be read")
        packaged_project = tomllib.loads(packaged_pyproject.read().decode("utf-8"))["project"]
        if (
            packaged_project.get("name") != project.name
            or packaged_project.get("version") != project.version
        ):
            _fail(
                f"{sdist_path.name} contains mismatched project identity: "
                f"name={packaged_project.get('name')!r} version={packaged_project.get('version')!r}"
            )


def _clean_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for variable in (
        "PYTHONHOME",
        "PYTHONPATH",
        "UV_PROJECT_ENVIRONMENT",
        "VIRTUAL_ENV",
    ):
        environment.pop(variable, None)
    environment["NO_COLOR"] = "1"
    environment["UV_NO_PROGRESS"] = "1"
    environment["UV_OFFLINE"] = "1"
    return environment


def run_checked(
    command: list[str],
    *,
    cwd: Path,
    description: str,
    echo_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=_clean_environment(),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        rendered_command = " ".join(command)
        _fail(
            f"{description} failed with exit code {result.returncode}: {rendered_command}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    if echo_output:
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
    return result


def _venv_executable(venv: Path, name: str) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / f"{name}.exe"
    return venv / "bin" / name


def _create_venv(uv: str, root: Path, name: str) -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    venv = root / name
    run_checked(
        [uv, "venv", "--python", "3.14", str(venv)],
        cwd=root,
        description=f"create {name}",
    )
    return venv, _venv_executable(venv, "python")


def verify_base_install(uv: str, wheel: Path, smoke_root: Path) -> None:
    venv, python = _create_venv(uv, smoke_root, "base-venv")
    run_checked(
        [uv, "pip", "install", "--python", str(python), str(wheel)],
        cwd=smoke_root,
        description="install base wheel",
    )
    help_result = run_checked(
        [str(_venv_executable(venv, "clearagent")), "--help"],
        cwd=smoke_root,
        description="run installed clearagent --help",
    )
    for command_name in ("chat", "eval", "trace"):
        if command_name not in help_result.stdout:
            _fail(f"installed clearagent --help did not list the {command_name!r} command")

    run_checked(
        [str(python), "-I", str(INSTALLED_SMOKE_SCRIPT), str(PROJECT_ROOT)],
        cwd=smoke_root,
        description="run installed wheel smoke",
        echo_output=True,
    )


def verify_pytest_extra(uv: str, wheel: Path, smoke_root: Path) -> None:
    _, python = _create_venv(uv, smoke_root, "pytest-venv")
    wheel_requirement = f"clearagent[pytest] @ {wheel.resolve().as_uri()}"
    run_checked(
        [uv, "pip", "install", "--python", str(python), wheel_requirement],
        cwd=smoke_root,
        description="install wheel with pytest extra",
    )
    help_result = run_checked(
        [str(python), "-I", "-m", "pytest", "--help"],
        cwd=smoke_root,
        description="load installed pytest plugin",
    )
    missing_flags = sorted(
        flag
        for flag in ("--clearagent-trace-db", "--clearagent-no-trace", "--clearagent-model")
        if flag not in help_result.stdout
    )
    if missing_flags:
        _fail(f"installed pytest plugin did not expose flags: {missing_flags!r}")


def run_distribution_gate(project_root: Path = PROJECT_ROOT) -> None:
    uv = shutil.which("uv")
    if uv is None:
        _fail("uv is required to build and verify distributions")

    project = load_project_metadata(project_root)
    with tempfile.TemporaryDirectory(prefix="clearagent-distribution-") as temporary:
        temporary_root = Path(temporary).resolve()
        if temporary_root.is_relative_to(project_root.resolve()):
            _fail(
                f"distribution verification directory must be outside the repository: {temporary_root}"
            )

        dist_directory = temporary_root / "dist"
        run_checked(
            [uv, "build", "--out-dir", str(dist_directory)],
            cwd=project_root,
            description="build sdist and wheel",
            echo_output=True,
        )
        artifacts = find_distribution_artifacts(dist_directory)
        inspect_sdist(artifacts.sdist, project, project_root=project_root)
        inspect_wheel(artifacts.wheel, project, project_root=project_root)
        run_checked(
            [
                sys.executable,
                "-m",
                "twine",
                "check",
                str(artifacts.sdist),
                str(artifacts.wheel),
            ],
            cwd=temporary_root,
            description="twine-check exact artifacts",
            echo_output=True,
        )
        verify_base_install(uv, artifacts.wheel, temporary_root / "base-smoke")
        verify_pytest_extra(uv, artifacts.wheel, temporary_root / "pytest-smoke")

        print(
            "distribution gate passed: "
            f"{artifacts.sdist.name}, {artifacts.wheel.name}, base install, pytest extra"
        )


def main() -> int:
    try:
        run_distribution_gate()
    except (DistributionCheckError, OSError, KeyError, tomllib.TOMLDecodeError) as error:
        print(f"distribution check failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
