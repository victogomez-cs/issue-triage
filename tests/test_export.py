import json

import pytest
import requests

from issue_triage.github import ExportError, export_issues
from issue_triage.repo import parse_repo


def _session(gh):
    s = requests.Session()
    s.post = gh.post
    return s


def test_paginates_and_normalizes(tmp_path, sample_issues, fake_github):
    gh = fake_github(sample_issues * 3)
    for k, i in enumerate(gh.issues):
        gh.issues[k] = dict(i, number=k + 1)
    path = export_issues(parse_repo("o/r"), tmp_path, token="t", page_size=4, session=_session(gh), sleep=lambda s: 0)
    blob = json.loads(path.read_text())
    assert blob["repo"] == "o/r" and len(blob["issues"]) == 30
    assert blob["issues"][0]["labels"] == ["bug", "priority: high", "area/ui"]
    assert not (tmp_path / "issues.partial.jsonl").exists()


def test_recovers_from_502_and_old_schema(tmp_path, sample_issues, fake_github):
    gh = fake_github(sample_issues, script={1: "no_issue_type", 3: "502"})
    path = export_issues(parse_repo("o/r"), tmp_path, token="t", page_size=4, session=_session(gh), sleep=lambda s: 0)
    assert len(json.loads(path.read_text())["issues"]) == len(sample_issues)


def test_resume_after_interruption(tmp_path, sample_issues, fake_github):
    gh = fake_github(sample_issues, script={3: "crash"})
    with pytest.raises(KeyboardInterrupt):
        export_issues(parse_repo("o/r"), tmp_path, token="t", page_size=4, session=_session(gh), sleep=lambda s: 0)
    assert (tmp_path / "export_checkpoint.json").exists()
    gh2 = fake_github(sample_issues)
    path = export_issues(parse_repo("o/r"), tmp_path, token="t", page_size=4, session=_session(gh2), sleep=lambda s: 0)
    blob = json.loads(path.read_text())
    assert sorted(i["number"] for i in blob["issues"]) == [i["number"] for i in sample_issues]
    assert gh2.calls < 4  # continued, not restarted


def test_clear_errors(tmp_path, sample_issues, fake_github, monkeypatch):
    with pytest.raises(ExportError, match="not found"):
        export_issues(parse_repo("o/r"), tmp_path, token="t", session=_session(fake_github([], missing=True)))
    with pytest.raises(ExportError, match="turned off"):
        export_issues(parse_repo("o/r"), tmp_path / "b", token="t", session=_session(fake_github([], has_issues=False)))
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("PATH", "")  # no `gh` either
    with pytest.raises(ExportError, match="token"):
        export_issues(parse_repo("o/r"), tmp_path / "c")
