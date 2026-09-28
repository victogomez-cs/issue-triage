"""Shared fixtures: synthetic issues, a fake GitHub GraphQL API and a fake Anthropic client."""

import json
import types

import pytest


def make_issue(number, title="Something is wrong", labels=(), body="Steps to reproduce: run it.", thumbs=0,
               updated="2026-09-01T00:00:00Z", issue_type=None, assignees=()):
    return {"number": number, "title": title, "body": body, "url": f"https://github.com/o/r/issues/{number}",
            "created_at": "2025-01-01T00:00:00Z", "updated_at": updated, "author": "someone",
            "labels": list(labels), "assignees": list(assignees), "milestone": None, "issue_type": issue_type,
            "comments": 1, "thumbs_up": thumbs}


@pytest.fixture
def issue():
    return make_issue


@pytest.fixture
def sample_issues():
    return [
        make_issue(1, "App crashes on launch", ["bug", "priority: high", "area/ui"], thumbs=12),
        make_issue(2, "Add dark mode", ["enhancement"], thumbs=40),
        make_issue(3, "Docs typo in README", ["documentation"]),
        make_issue(4, "How do I configure X?", ["question"]),
        make_issue(5, "Scrolling is janky", ["bug", "performance", "priority: low"]),
        make_issue(6, "Button misaligned on Safari"),
        make_issue(7, "Button misaligned on Safari browser", body="Same as the other one I think"),
        make_issue(8, "Release 1.2.3 tracking", ["tracking issue"]),
        make_issue(9, "Release 1.2.4 tracking", ["tracking issue"]),
        make_issue(10, "Memory leak in cache", ["bug", "regression", "priority: low"], thumbs=3),
    ]


def to_node(i):
    return {"number": i["number"], "title": i["title"], "body": i["body"], "url": i["url"],
            "createdAt": i["created_at"], "updatedAt": i["updated_at"], "author": {"login": i["author"]},
            "labels": {"nodes": [{"name": l} for l in i["labels"]]},
            "assignees": {"nodes": [{"login": a} for a in i["assignees"]]}, "milestone": None,
            "issueType": {"name": i["issue_type"]} if i["issue_type"] else None,
            "comments": {"totalCount": i["comments"]}, "reactions": {"totalCount": i["thumbs_up"]}}


class FakeResponse:
    def __init__(self, status, payload=None, headers=None):
        self.status_code, self._payload, self.headers = status, payload, headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeGitHub:
    """Serves issues page by page; `script` maps call number -> a special response."""

    def __init__(self, issues, script=None, has_issues=True, missing=False):
        self.issues, self.script, self.calls = issues, script or {}, 0
        self.has_issues, self.missing = has_issues, missing

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls += 1
        special = self.script.get(self.calls)
        if special == "no_issue_type" and "issueType" in json["query"]:
            return FakeResponse(200, {"errors": [{"message": "Field 'issueType' doesn't exist on type 'Issue'"}]})
        if special == "502":
            return FakeResponse(502)
        if special == "crash":
            raise KeyboardInterrupt
        if self.missing:
            return FakeResponse(200, {"data": {"repository": None},
                                      "errors": [{"type": "NOT_FOUND", "message": "Could not resolve to a Repository"}]})
        v = json["variables"]
        start, size = int(v["cursor"] or 0), v["pageSize"]
        page = self.issues[start:start + size]
        end = start + len(page)
        return FakeResponse(200, {"data": {"repository": {"hasIssuesEnabled": self.has_issues, "issues": {
            "totalCount": len(self.issues), "pageInfo": {"hasNextPage": end < len(self.issues), "endCursor": str(end)},
            "nodes": [to_node(i) for i in page]}}, "rateLimit": {"remaining": 5000, "resetAt": "", "cost": 1}}})


@pytest.fixture
def fake_github():
    return FakeGitHub


class _Block:
    type = "tool_use"

    def __init__(self, data):
        self.input = data


def fake_answer(number, title=""):
    kind = "bug" if ("crash" in title.lower() or number % 2) else "feature_request"
    return {"kind": kind, "severity": "critical" if "crash" in title.lower() else ("medium" if kind == "bug" else "n/a"),
            "impact_type": "crash_or_hang", "has_reproduction": True, "affects_production": False,
            "confidence": 0.9, "summary": f"Summary {number}"}


class FakeAnthropic:
    """Answers every request with a deterministic classification; records what was sent."""

    def __init__(self, *a, **k):
        self.sent = []
        self.messages = types.SimpleNamespace(create=self._create, batches=_FakeBatches())

    def _create(self, **params):
        self.sent.append(params)
        content = params["messages"][0]["content"]
        number = int(content.split("#", 1)[1].split(":", 1)[0])
        title = content.split(":", 1)[1].split("\n", 1)[0]
        return types.SimpleNamespace(content=[_Block(fake_answer(number, title))])


class _FakeBatches:
    def __init__(self):
        self.store = {}

    def create(self, requests):
        bid = f"batch_{len(self.store)}"
        self.store[bid] = list(requests)
        return types.SimpleNamespace(id=bid)

    def retrieve(self, bid):
        return types.SimpleNamespace(processing_status="ended", request_counts=types.SimpleNamespace(
            succeeded=len(self.store[bid]), errored=0, processing=0))

    def results(self, bid):
        for r in self.store[bid]:
            number = int(r["custom_id"].split("-")[1])
            if number == 3:  # one failed request
                yield types.SimpleNamespace(custom_id=r["custom_id"], result=types.SimpleNamespace(type="errored"))
                continue
            msg = types.SimpleNamespace(content=[_Block(fake_answer(number))])
            yield types.SimpleNamespace(custom_id=r["custom_id"],
                                        result=types.SimpleNamespace(type="succeeded", message=msg))


@pytest.fixture
def fake_anthropic():
    return FakeAnthropic


def write_export(path, issues, repo="o/r"):
    path.mkdir(parents=True, exist_ok=True)
    (path / "issues.json").write_text(json.dumps(
        {"repo": repo, "host": "github.com", "exported_at": "2026-09-28T00:00:00Z", "issues": issues}))
