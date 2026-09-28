from issue_triage.dedupe import find_duplicates, is_series
from issue_triage.profile import load_profile


def test_finds_near_identical_and_skips_series(sample_issues):
    res = find_duplicates(sample_issues, load_profile("generic"))
    pairs = {(p["a"], p["b"]) for p in res["pairs"]}
    assert (6, 7) in pairs
    assert (8, 9) not in pairs  # internal tracking issues are skipped by default


def test_series_rule():
    assert is_series("Flutter Release Version 3.47.6", "Flutter Release Version 3.47.5")
    assert is_series("security: fix CVE-2026-1234", "security: fix CVE-2026-5678")
    assert not is_series("Crash on iOS", "Crash on iOS")          # identical titles may be real duplicates
    assert is_series("[image_picker] Support restrictions on Windows", "[image_picker] Support restrictions on Linux")
    assert not is_series("Crash on startup", "Crash on shutdown")


def test_tiny_and_empty_corpora(issue):
    p = load_profile("generic")
    assert find_duplicates([], p)["pairs"] == []
    assert find_duplicates([issue(1, "a")], p)["pairs"] == []
    assert isinstance(find_duplicates([issue(1, "x", body=""), issue(2, "y", body="")], p)["pairs"], list)
