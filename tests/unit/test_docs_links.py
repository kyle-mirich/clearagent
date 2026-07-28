from scripts.check_docs_links import find_broken_links, find_docs_issues, find_orphan_docs


def test_markdown_links_point_to_existing_local_files():
    broken = find_broken_links(".")

    assert broken == []


def test_broken_links_are_reported_relative_to_the_checked_root(tmp_path, monkeypatch):
    (tmp_path / "README.md").write_text(
        "[missing](docs/missing.md)\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    assert find_broken_links(".") == ["README.md: missing docs/missing.md"]


def test_local_heading_anchors_are_validated(tmp_path):
    docs_path = tmp_path / "docs"
    docs_path.mkdir()
    (tmp_path / "README.md").write_text(
        "[valid](docs/guide.md#public-api)\n[missing](docs/guide.md#private-api)\n",
        encoding="utf-8",
    )
    (docs_path / "guide.md").write_text(
        "# Guide\n\n## `Public` API\n",
        encoding="utf-8",
    )

    assert find_broken_links(tmp_path) == [
        "README.md: missing anchor #private-api in docs/guide.md"
    ]


def test_same_page_anchors_ignore_headings_inside_code_fences(tmp_path):
    (tmp_path / "README.md").write_text(
        "# Real Heading\n\n[real](#real-heading)\n[example](#example-heading)\n\n"
        "```markdown\n# Example Heading\n```\n",
        encoding="utf-8",
    )

    assert find_broken_links(tmp_path) == [
        "README.md: missing anchor #example-heading in README.md"
    ]


def test_docs_pages_must_be_listed_from_site(tmp_path):
    docs_path = tmp_path / "docs"
    docs_path.mkdir()
    (docs_path / "site.md").write_text("# Docs\n\n- [Guide](guide.md)\n", encoding="utf-8")
    (docs_path / "guide.md").write_text("# Guide\n", encoding="utf-8")
    (docs_path / "orphan.md").write_text("# Orphan\n", encoding="utf-8")

    assert find_orphan_docs(tmp_path) == [
        "docs/site.md: unlisted documentation page docs/orphan.md"
    ]
    assert find_docs_issues(tmp_path) == [
        "docs/site.md: unlisted documentation page docs/orphan.md"
    ]
