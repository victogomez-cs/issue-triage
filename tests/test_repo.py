import pytest

from issue_triage.repo import parse_repo


@pytest.mark.parametrize("text,slug,host", [
    ("flutter/flutter", "flutter/flutter", "github.com"),
    ("https://github.com/flutter/flutter/issues?q=is%3Aissue+is%3Aopen", "flutter/flutter", "github.com"),
    ("https://github.com/facebook/react.git", "facebook/react", "github.com"),
    ("git@github.com:rust-lang/rust.git", "rust-lang/rust", "github.com"),
    ("github.com/microsoft/vscode", "microsoft/vscode", "github.com"),
    ("http://www.github.com/a/b/", "a/b", "github.com"),
    ("https://ghe.example.com/team/app/issues/12", "team/app", "ghe.example.com"),
])
def test_parse(text, slug, host):
    r = parse_repo(text)
    assert (r.slug, r.host) == (slug, host)


def test_enterprise_api_url():
    assert parse_repo("https://ghe.example.com/t/a").graphql_url == "https://ghe.example.com/api/graphql"
    assert parse_repo("t/a").graphql_url == "https://api.github.com/graphql"


@pytest.mark.parametrize("bad", ["", "justname", "https://github.com/onlyowner", "a/b/c/d e"])
def test_rejects(bad):
    with pytest.raises(ValueError):
        parse_repo(bad)
