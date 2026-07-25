from scripts.check_docs_links import find_broken_links


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
