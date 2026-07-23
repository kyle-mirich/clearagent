from scripts.check_docs_links import find_broken_links


def test_markdown_links_point_to_existing_local_files():
    broken = find_broken_links(".")

    assert broken == []
