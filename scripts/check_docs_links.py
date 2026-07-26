from __future__ import annotations

import argparse
import re
from pathlib import Path
from urllib.parse import unquote, urlparse


LINK_PATTERN = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
IGNORED_SCHEMES = {"http", "https", "mailto"}


def find_broken_links(root: str | Path) -> list[str]:
    repo_root = Path(root).resolve()
    broken = []
    for markdown_path in sorted(repo_root.rglob("*.md")):
        if _is_ignored_path(markdown_path):
            continue
        for target in _local_link_targets(markdown_path):
            if not target.exists():
                relative_source = markdown_path.relative_to(repo_root)
                try:
                    displayed_target = target.relative_to(repo_root)
                except ValueError:
                    displayed_target = target
                broken.append(f"{relative_source}: missing {displayed_target}")
    return broken


def _local_link_targets(markdown_path: Path) -> list[Path]:
    targets = []
    for match in LINK_PATTERN.finditer(markdown_path.read_text(encoding="utf-8")):
        raw_target = match.group(1).strip()
        if not raw_target or raw_target.startswith("#"):
            continue
        parsed = urlparse(raw_target)
        if parsed.scheme in IGNORED_SCHEMES:
            continue
        path_part = unquote(parsed.path)
        if not path_part:
            continue
        target = Path(path_part)
        if not target.is_absolute():
            target = markdown_path.parent / target
        targets.append(target.resolve())
    return targets


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
    parser = argparse.ArgumentParser(description="Validate local links in Markdown files.")
    parser.add_argument("root", nargs="?", default=".")
    args = parser.parse_args()
    broken = find_broken_links(args.root)
    if broken:
        for item in broken:
            print(item)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
