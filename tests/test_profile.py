import pytest

from issue_triage.profile import ProfileError, load_profile


@pytest.fixture
def generic():
    return load_profile("generic")


@pytest.fixture
def flutter():
    return load_profile("auto", repo="flutter/flutter")


def test_auto_picks_flutter(flutter):
    assert flutter.name == "flutter"
    assert load_profile("auto", repo="someone/else").name == "generic"


@pytest.mark.parametrize("labels,kind", [
    (["bug"], "bug"), (["kind/bug"], "bug"), (["C-bug"], "bug"), (["Type: Bug"], "bug"),
    (["enhancement"], "feature"), (["feature-request"], "feature"), (["FeatureRequest"], "feature"),
    (["kind/feature"], "feature"), (["documentation"], "docs"), (["question"], "question"),
    (["kind/flake"], "internal"), (["kind/failing-test"], "internal"), (["dependencies"], "internal"),
    (["wontfix"], "not_actionable"), (["duplicate"], "not_actionable"),
    (["enhancement", "regression"], "bug"),   # strong bug signal wins
    (["sig/testing"], "uncategorized"),       # a team label, not a kind
    ([], "uncategorized"),
])
def test_generic_kinds(generic, labels, kind):
    assert generic.kind(labels) == kind


def test_issue_type_used_when_no_label(generic):
    assert generic.kind([], "Bug") == "bug"
    assert generic.kind(["enhancement"], "Bug") == "feature"  # labels first


@pytest.mark.parametrize("label,pri", [
    ("P0", "P0"), ("priority: critical", "P0"), ("priority/critical-urgent", "P0"), ("release-blocker", "P0"),
    ("P-high", "P1"), ("priority/important-soon", "P1"), ("priority:medium", "P2"),
    ("priority/important-longterm", "P2"), ("priority/backlog", "P3"), ("low priority", "P3"),
])
def test_generic_priority(generic, label, pri):
    assert generic.priority([label]) == pri


@pytest.mark.parametrize("label,team", [
    ("area/ui", "ui"), ("sig/node", "node"), ("T-compiler", "compiler"), ("A-LLVM", "LLVM"),
    ("component: editor", "editor"),
])
def test_generic_team(generic, label, team):
    assert generic.team([label]) == team


def test_generic_signals(generic):
    assert generic.has("reproducible", ["S-has-mcve"])
    assert generic.has("needs_info", ["info-needed"])
    assert generic.has("waiting", ["WaitingForInfo"])
    assert generic.has("needs_triage", ["needs-triage"])
    assert generic.has("crash", ["I-ICE"])


def test_flutter_rules(flutter):
    assert flutter.kind(["c: crash", "c: proposal"]) == "bug"
    assert flutter.kind(["c: proposal"]) == "feature"
    assert flutter.kind(["d: api docs"]) == "docs"
    assert flutter.severity(["c: crash", "c: regression"]) == "critical"
    assert flutter.severity(["c: crash"]) == "high"
    assert flutter.severity(["c: rendering"]) == "medium"
    assert flutter.team(["team-framework"]) == "framework"
    assert flutter.has("named_customer", ["customer: acme"])
    assert not flutter.has("named_customer", ["customer: crowd"])
    assert flutter.release(["found in release: 3.44", "found in release: 3.7"]) == (3, 44)


def test_flutter_triage_status(flutter):
    st = lambda labels: flutter.triage_status(labels, [], flutter.priority(labels), flutter.kind(labels), True)  # noqa: E731
    assert st([]) == "Needs primary triage"
    assert st(["team-engine"]) == "Needs team triage"
    assert st(["team-engine", "triaged-engine"]) == "Triaged, no priority"
    assert st(["team-engine", "triaged-engine", "P2"]) == "Triaged"
    assert st(["waiting for response", "team-engine"]) == "Waiting for reporter"


def test_user_config_merges(tmp_path):
    cfg = tmp_path / "triage.toml"
    cfg.write_text("""
profile = "generic"
project_context = "An SDK."
[[kind_rules]]
kind = "bug"
labels = ['enhancement']
[priority]
P0 = ['sev-0']
[weights.priority]
P0 = 99
""")
    p = load_profile(None, "o/r", cfg)
    assert p.kind(["enhancement"]) == "bug"          # user rule checked first
    assert p.kind(["question"]) == "question"        # profile rules still apply
    assert p.priority(["sev-0"]) == "P0"
    assert p.priority(["P0"]) is None                # P0 list replaced
    assert p.priority(["P1"]) == "P1"                # other levels kept
    assert p.weights["priority"]["P0"] == 99
    assert p.weights["priority"]["P1"] == 30
    assert p.project_context == "An SDK."


def test_bad_regex_and_unknown_profile(tmp_path):
    cfg = tmp_path / "bad.toml"
    cfg.write_text("[[kind_rules]]\nkind = 'bug'\nlabels = ['(']\n")
    with pytest.raises(ProfileError):
        load_profile(None, "o/r", cfg)
    with pytest.raises(ProfileError):
        load_profile("nope")
    cfg.write_text("[[kind_rules]]\nkind = 'bogus'\nlabels = ['x']\n")
    with pytest.raises(ProfileError):
        load_profile(None, "o/r", cfg)
