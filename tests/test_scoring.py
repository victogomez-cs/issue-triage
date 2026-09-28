from issue_triage.profile import load_profile
from issue_triage.scoring import build_context, score_issue


def run(profile, issues, ai=None):
    ctx = build_context(issues, profile, "2026-09-28T00:00:00Z")
    return [score_issue(i, (ai or {}).get(i["number"]), ctx) for i in issues]


def test_flutter_crash_ranks_high(issue):
    p = load_profile("flutter")
    rows = run(p, [
        issue(1, "Crash when opening camera", ["c: crash", "P1", "team-engine", "triaged-engine",
                                                "has reproducible steps"], thumbs=20),
        issue(2, "Wrong padding", ["P3", "team-framework", "triaged-framework"]),
    ])
    assert rows[0]["kind"] == "Bug" and rows[0]["severity"] == "High"
    assert rows[0]["urgency"] in ("Critical", "High") and rows[0]["score"] > rows[1]["score"]
    assert rows[1]["kind"] == "Bug (assumed)"  # Flutter: no c: label means a plain bug


def test_p0_floor(issue):
    p = load_profile("flutter")
    [r] = run(p, [issue(1, "x", ["P0", "team-engine", "triaged-engine", "waiting for response"])])
    assert r["urgency"] == "Critical"


def test_human_labels_win_and_ai_flags(issue):
    p = load_profile("generic")
    issues = [issue(1, "Add a way to do X", ["enhancement"]), issue(2, "It's broken")]
    ai = {1: {"kind": "bug", "severity": "high", "confidence": 0.95},
          2: {"kind": "bug", "severity": "critical", "confidence": 0.9, "summary": "Broken."}}
    r1, r2 = run(p, issues, ai)
    assert r1["kind"] == "Feature / proposal" and "AI: looks like a bug, labeled feature" in r1["flags"]
    assert r2["kind"] == "Bug" and r2["severity"] == "Critical" and r2["ai_summary"] == "Broken."


def test_low_confidence_ai_does_not_fill(issue):
    p = load_profile("generic")
    [r] = run(p, [issue(1, "Something")], {1: {"kind": "bug", "severity": "high", "confidence": 0.3}})
    assert r["kind"] == "Uncategorized"


def test_title_keyword_severity_without_ai(issue):
    p = load_profile("generic")
    [r] = run(p, [issue(1, "App crashes on startup", ["bug"])])
    assert r["severity"] == "High" and "title keyword" in r["score_breakdown"]


def test_review_flags(issue):
    p = load_profile("flutter")
    rows = run(p, [
        issue(1, "x", ["c: crash", "P3", "team-engine", "triaged-engine"]),
        issue(2, "y", ["c: regression", "P3", "team-engine", "triaged-engine"]),
        issue(3, "z", ["P3", "team-engine", "triaged-engine"], thumbs=150),
        issue(4, "w", [], updated="2020-01-01T00:00:00Z"),
    ])
    assert "Crash at P3" in rows[0]["flags"]
    assert "Regression at P3" in rows[1]["flags"]
    assert "Popular (150 thumbs-up) at P3" in rows[2]["flags"]
    assert "Needs primary triage" in rows[3]["flags"] and "Stale 2+ years" in rows[3]["flags"]


def test_generic_priority_requirement_is_auto(issue):
    p = load_profile("generic")
    # hardly anyone uses priority labels -> don't demand them
    rows = run(p, [issue(i, "t", ["bug"]) for i in range(1, 11)] + [issue(11, "t", ["bug", "P1"])])
    assert all("Triaged, no priority" not in r["flags"] for r in rows)
    # most issues have one -> flag the bug without
    rows = run(p, [issue(i, "t", ["bug", "P2"]) for i in range(1, 6)] + [issue(6, "t", ["bug"])])
    assert "Triaged, no priority" in rows[-1]["flags"]
