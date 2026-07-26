"""Build and verify ClearAgent exactly as an external consumer installs it."""

from __future__ import annotations

import configparser
from dataclasses import dataclass
from email.parser import Parser
import os
from pathlib import Path
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

REQUIRED_PACKAGE_FILES = {
    "clearagent/__init__.py",
    "clearagent/agent.py",
    "clearagent/cli.py",
    "clearagent/chat/app.py",
    "clearagent/chat/static/index.html",
    "clearagent/chat/static/styles.css",
    "clearagent/chat/static/app.js",
}


class DistributionCheckError(RuntimeError):
    """Raised when a built distribution violates a release invariant."""


@dataclass(frozen=True)
class ProjectMetadata:
    name: str
    version: str
    description: str
    requires_python: str
    license_expression: str


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

    return ProjectMetadata(
        name=project["name"],
        version=project["version"],
        description=project["description"],
        requires_python=project["requires-python"],
        license_expression=license_expression,
    )


def find_distribution_artifacts(dist_directory: Path) -> DistributionArtifacts:
    files = sorted(
        path
        for path in dist_directory.iterdir()
        if path.is_file() and path.name != ".gitignore"
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


def _require_nonempty_zip_members(
    archive: zipfile.ZipFile,
    members: set[str],
    artifact: Path,
) -> None:
    empty = sorted(member for member in members if archive.getinfo(member).file_size == 0)
    if empty:
        _fail(f"{artifact.name} contains empty required members: {empty!r}")


def inspect_wheel(wheel_path: Path, project: ProjectMetadata) -> None:
    with zipfile.ZipFile(wheel_path) as wheel:
        members = set(wheel.namelist())
        metadata_members = sorted(
            member for member in members if member.endswith(".dist-info/METADATA")
        )
        if len(metadata_members) != 1:
            _fail(
                f"{wheel_path.name} must contain exactly one .dist-info/METADATA file; "
                f"found={metadata_members!r}"
            )

        dist_info = metadata_members[0].removesuffix("METADATA")
        required = REQUIRED_PACKAGE_FILES | {
            f"{dist_info}METADATA",
            f"{dist_info}WHEEL",
            f"{dist_info}RECORD",
            f"{dist_info}entry_points.txt",
            f"{dist_info}licenses/LICENSE",
        }
        _missing_members(members, required, wheel_path)
        _require_nonempty_zip_members(wheel, required - {f"{dist_info}RECORD"}, wheel_path)

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


def inspect_sdist(sdist_path: Path, project: ProjectMetadata) -> None:
    expected_root = f"{project.name}-{project.version}"
    required_relative = {
        "LICENSE",
        "README.md",
        "pyproject.toml",
        "src/clearagent/__init__.py",
        "src/clearagent/agent.py",
        "src/clearagent/cli.py",
        "src/clearagent/chat/app.py",
        "src/clearagent/chat/static/index.html",
        "src/clearagent/chat/static/styles.css",
        "src/clearagent/chat/static/app.js",
    }
    required = {f"{expected_root}/{member}" for member in required_relative}

    with tarfile.open(sdist_path, mode="r:gz") as sdist:
        regular_files = {member.name for member in sdist.getmembers() if member.isfile()}
        _missing_members(regular_files, required, sdist_path)
        empty = sorted(
            member_name
            for member_name in required
            if sdist.getmember(member_name).size == 0
        )
        if empty:
            _fail(f"{sdist_path.name} contains empty required members: {empty!r}")

        packaged_pyproject = sdist.extractfile(f"{expected_root}/pyproject.toml")
        if packaged_pyproject is None:
            _fail(f"{sdist_path.name} pyproject.toml could not be read")
        packaged_project = tomllib.loads(packaged_pyproject.read().decode("utf-8"))["project"]
        if packaged_project.get("name") != project.name or packaged_project.get(
            "version"
        ) != project.version:
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
            _fail(f"distribution verification directory must be outside the repository: {temporary_root}")

        dist_directory = temporary_root / "dist"
        run_checked(
            [uv, "build", "--out-dir", str(dist_directory)],
            cwd=project_root,
            description="build sdist and wheel",
            echo_output=True,
        )
        artifacts = find_distribution_artifacts(dist_directory)
        inspect_sdist(artifacts.sdist, project)
        inspect_wheel(artifacts.wheel, project)
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
