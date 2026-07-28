from __future__ import annotations

import argparse
import html
import re
from pathlib import Path
from urllib.parse import unquote, urlparse


LINK_PATTERN = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
HEADING_PATTERN = re.compile(r"^#{1,6}[ \t]+(.+?)\s*$")
IGNORED_SCHEMES = {"http", "https", "mailto"}


def find_broken_links(root: str | Path) -> list[str]:
    repo_root = Path(root).resolve()
    broken = []
    for markdown_path in sorted(repo_root.rglob("*.md")):
        if _is_ignored_path(markdown_path):
            continue
        for target, fragment in _local_link_targets(markdown_path):
            if not target.exists():
                broken.append(
                    f"{_relative_path(markdown_path, repo_root)}: "
                    f"missing {_relative_path(target, repo_root)}"
                )
                continue
            if fragment and target.suffix.lower() == ".md":
                anchor = unquote(fragment)
                if anchor not in _markdown_anchors(target):
                    broken.append(
                        f"{_relative_path(markdown_path, repo_root)}: "
                        f"missing anchor #{anchor} in {_relative_path(target, repo_root)}"
                    )
    return broken


def find_orphan_docs(root: str | Path) -> list[str]:
    """Report Markdown pages under docs/ that are absent from docs/site.md."""

    repo_root = Path(root).resolve()
    docs_root = repo_root / "docs"
    site_path = docs_root / "site.md"
    if not site_path.exists():
        return []

    listed_pages = {
        target
        for target, _ in _local_link_targets(site_path)
        if target.suffix.lower() == ".md" and _is_within(target, docs_root)
    }
    orphaned = []
    for markdown_path in sorted(docs_root.rglob("*.md")):
        resolved_path = markdown_path.resolve()
        if resolved_path == site_path.resolve() or _is_ignored_path(markdown_path):
            continue
        if resolved_path not in listed_pages:
            orphaned.append(
                f"docs/site.md: unlisted documentation page "
                f"{_relative_path(resolved_path, repo_root)}"
            )
    return orphaned


def find_docs_issues(root: str | Path) -> list[str]:
    return [*find_broken_links(root), *find_orphan_docs(root)]


def _local_link_targets(markdown_path: Path) -> list[tuple[Path, str]]:
    targets = []
    for match in LINK_PATTERN.finditer(markdown_path.read_text(encoding="utf-8")):
        raw_target = match.group(1).strip()
        if not raw_target:
            continue
        parsed = urlparse(raw_target)
        if parsed.scheme in IGNORED_SCHEMES:
            continue
        path_part = unquote(parsed.path)
        if path_part:
            target = Path(path_part)
            if not target.is_absolute():
                target = markdown_path.parent / target
        else:
            target = markdown_path
        targets.append((target.resolve(), parsed.fragment))
    return targets


def _markdown_anchors(markdown_path: Path) -> set[str]:
    anchors = set()
    anchor_counts: dict[str, int] = {}
    fence_marker: str | None = None
    for line in markdown_path.read_text(encoding="utf-8").splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if fence_marker is None:
                fence_marker = marker
            elif fence_marker == marker:
                fence_marker = None
            continue
        if fence_marker is not None:
            continue
        match = HEADING_PATTERN.match(line)
        if not match:
            continue
        heading = re.sub(r"\s+#+\s*$", "", match.group(1))
        base_anchor = _github_heading_anchor(heading)
        occurrence = anchor_counts.get(base_anchor, 0)
        anchor_counts[base_anchor] = occurrence + 1
        anchor = base_anchor if occurrence == 0 else f"{base_anchor}-{occurrence}"
        anchors.add(anchor)
    return anchors


def _github_heading_anchor(heading: str) -> str:
    heading = html.unescape(heading)
    heading = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", heading)
    heading = re.sub(r"<[^>]+>", "", heading)
    heading = heading.replace("`", "").lower().strip()
    heading = "".join(
        character for character in heading if character.isalnum() or character in {" ", "-", "_"}
    )
    return re.sub(r"\s", "-", heading)


def _relative_path(path: Path, root: Path) -> Path:
    try:
        return path.relative_to(root)
    except ValueError:
        return path


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
    except ValueError:
        return False
    return True


def _is_ignored_path(path: Path) -> bool:
    ignored_parts = {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
    }
    return any(part in ignored_parts for part in path.parts)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate Markdown files, heading anchors, and the docs index."
    )
    parser.add_argument("root", nargs="?", default=".")
    args = parser.parse_args()
    issues = find_docs_issues(args.root)
    if issues:
        for item in issues:
            print(item)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
