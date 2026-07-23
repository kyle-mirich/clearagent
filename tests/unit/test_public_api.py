import clearagent


def test_public_api_stays_small():
    assert clearagent.__all__ == ["create_agent", "tool"]
