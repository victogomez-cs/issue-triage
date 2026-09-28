import json

import pytest

from issue_triage import ai
from issue_triage.profile import load_profile


def test_sync_classify_and_cache(tmp_path, sample_issues, fake_anthropic):
    client = fake_anthropic()
    p = load_profile("generic")
    s = ai.classify(sample_issues, tmp_path, p, "o/r", mode="sync", client=client, assume_yes=True)
    assert s["saved"] == len(sample_issues) and s["failed"] == 0
    assert "o/r" in client.sent[0]["system"] and client.sent[0]["tool_choice"]["name"] == "record_triage"
    results = ai.load_results(tmp_path)
    assert results[1]["severity"] == "critical"
    # second run: everything cached, nothing sent
    client2 = fake_anthropic()
    s2 = ai.classify(sample_issues, tmp_path, p, "o/r", mode="sync", client=client2, assume_yes=True)
    assert s2["to_classify"] == 0 and client2.sent == []


def test_edited_issue_is_reclassified(tmp_path, sample_issues, fake_anthropic):
    p = load_profile("generic")
    ai.classify(sample_issues, tmp_path, p, "o/r", mode="sync", client=fake_anthropic(), assume_yes=True)
    sample_issues[0]["updated_at"] = "2026-09-27T00:00:00Z"
    s = ai.classify(sample_issues, tmp_path, p, "o/r", mode="sync", client=fake_anthropic(), assume_yes=True)
    assert s["to_classify"] == 1


def test_batch_mode_with_a_failure(tmp_path, sample_issues, fake_anthropic):
    p = load_profile("generic")
    s = ai.classify(sample_issues, tmp_path, p, "o/r", mode="batch", client=fake_anthropic(), assume_yes=True,
                    poll_seconds=0)
    assert s["mode"] == "batch" and s["failed"] == 1 and s["saved"] == len(sample_issues) - 1
    assert 3 not in ai.load_results(tmp_path)
    assert not (tmp_path / "ai_batches.json").exists()


def test_scope_and_dry_run(tmp_path, sample_issues, fake_anthropic):
    p = load_profile("generic")
    s = ai.classify(sample_issues, tmp_path, p, "o/r", scope="uncategorized", dry_run=True, client=fake_anthropic())
    assert s["to_classify"] == 2  # issues 6 and 7 have no labels
    assert not (tmp_path / "ai_results.jsonl").exists()


def test_needs_key(tmp_path, sample_issues, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ai.AIError):
        ai.classify(sample_issues, tmp_path, load_profile("generic"), "o/r")


def test_import_json_and_jsonl(tmp_path):
    data = tmp_path / "data"
    arr = tmp_path / "page.json"
    arr.write_text(json.dumps([{"number": 5, "kind": "bug", "severity": "low", "confidence": 0.9}]))
    assert ai.import_results(arr, data) == 1
    lines = tmp_path / "more.jsonl"
    lines.write_text(json.dumps({"number": 5, "kind": "docs", "severity": "n/a", "confidence": 0.9}) + "\n")
    assert ai.import_results(lines, data) == 1
    assert ai.load_results(data)[5]["kind"] == "docs"  # .jsonl wins over .json


def test_parse_message_rejects_unknown_kind():
    import types
    blk = types.SimpleNamespace(type="tool_use", input={"kind": "banana"})
    assert ai.parse_message(types.SimpleNamespace(content=[blk])) is None
