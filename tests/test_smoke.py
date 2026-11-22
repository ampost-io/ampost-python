"""Against the real API, only when AMPOST_API_KEY is set. Reads and validates; creates nothing."""

from __future__ import annotations

import os

import pytest

from ampost import Ampost, AuthenticationError

API_KEY = os.environ.get("AMPOST_API_KEY")
pytestmark = pytest.mark.skipif(not API_KEY, reason="AMPOST_API_KEY is not set")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Ampost:
    # conftest clears AMPOST_* for unit tests; the smoke test needs them back.
    assert API_KEY
    monkeypatch.setenv("AMPOST_API_KEY", API_KEY)
    for name in ("AMPOST_BASE_URL",):
        if os.environ.get(name):
            monkeypatch.setenv(name, os.environ[name])
    return Ampost(api_key=API_KEY)


def test_lists_platforms_with_their_rules(client: Ampost) -> None:
    platforms = client.platforms.list()
    names = {p["platform"] for p in platforms}
    assert "bluesky" in names and all("text" in p for p in platforms.data)


def test_reads_one_page_of_posts_and_accounts(client: Ampost) -> None:
    posts = client.posts.list(limit=1)
    assert posts.pagination["limit"] == 1
    accounts = client.accounts.list(limit=5)
    assert isinstance(accounts.data, list)


def test_validates_a_post_without_creating_it(client: Ampost) -> None:
    result = client.posts.validate({"text": "Hello from the ampost-python smoke test", "targets": [{"platforms": ["bluesky"]}]})
    assert isinstance(result["valid"], bool)


def test_answers_a_wrong_key_with_authentication_error() -> None:
    with pytest.raises(AuthenticationError):
        Ampost(api_key="amp_wrong_key_for_smoke_test", max_retries=0).posts.list(limit=1)
