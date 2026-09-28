"""Label profiles: how a repository's labels map to kind, priority, severity, team and signals."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

KINDS = ("bug", "feature", "internal", "docs", "question", "not_actionable")
PRIORITIES = ("P0", "P1", "P2", "P3")
SEVERITIES = ("critical", "high", "medium", "low")
SEV_ORDER = ["unrated", "low", "medium", "high", "critical"]
SIGNALS = ("crash", "regression", "production", "named_customer", "crowd_customer", "reproducible", "needs_info",
           "waiting", "needs_triage")

# Built-in profiles chosen automatically for these repositories (lower-case owner/name).
AUTO_PROFILES = {"flutter/flutter": "flutter"}

DEFAULT_WEIGHTS = {
    "priority": {"P0": 45, "P1": 30, "P2": 12, "P3": 0, "none": 8},
    "severity": {"critical": 30, "high": 20, "medium": 10, "low": 2, "unrated": 6},
    "signals": {
        "production": 10, "ai_production": 5, "named_customer": 10, "crowd_customer": 6,
        "reproducible": 8, "ai_reproducible": 5, "needs_info": -8, "waiting": -12,
        "recent_release": 6, "recently_active": 3, "stale": -4,
    },
    "thumbs_up_scale": 4.0,        # points = scale * log2(1 + thumbs_up), capped
    "thumbs_up_cap": 20.0,
    "urgency_thresholds": {"Critical": 70, "High": 48, "Medium": 28},
    "urgency_floors": {"P0": "Critical", "P1": "High"},
    "recent_days": 90,
    "stale_days": 730,
    "popular_thumbs_up": 100,
    "ai_min_confidence_fill": 0.6,
    "ai_min_confidence_flag": 0.8,
    "recent_release_count": 3,
}


class ProfileError(ValueError):
    pass


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def builtin_profiles() -> list[str]:
    return sorted(p.name[:-5] for p in resources.files("issue_triage.profiles").iterdir()
                  if p.name.endswith(".toml"))


def _load_builtin(name: str) -> dict:
    f = resources.files("issue_triage.profiles") / f"{name}.toml"
    if not f.is_file():
        raise ProfileError(f"Unknown profile '{name}'. Built-in profiles: {', '.join(builtin_profiles())}")
    return tomllib.loads(f.read_text(encoding="utf-8"))


def load_profile(name: str | None, repo: str | None = None, config_path: str | Path | None = None) -> "Profile":
    """Load a built-in profile ('auto' picks by repo), then apply a user config file on top.

    In the user config, tables merge key by key. kind_rules / severity_rules from the config are
    checked BEFORE the profile's rules, unless the config sets replace_rules = true.
    """
    user = {}
    if config_path:
        p = Path(config_path)
        if not p.exists():
            raise ProfileError(f"Config file not found: {p}")
        try:
            user = tomllib.loads(p.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as e:
            raise ProfileError(f"{p}: {e}") from e
    name = user.pop("profile", None) or name or "auto"
    if name == "auto":
        name = AUTO_PROFILES.get((repo or "").lower(), "generic")
    data = _load_builtin(name)

    replace = bool(user.pop("replace_rules", False))
    for key in ("kind_rules", "severity_rules"):
        if key in user:
            data[key] = user.pop(key) + ([] if replace else data.get(key, []))
    data = _deep_merge(data, user)
    return Profile(data, source=name + (f" + {config_path}" if config_path else ""))


@dataclass
class Profile:
    raw: dict
    source: str = ""
    weights: dict = field(init=False)

    def __post_init__(self):
        r = self.raw
        self.name = r.get("name", "custom")
        self.flags = 0 if r.get("case_sensitive") else re.IGNORECASE
        self.uncategorized_is_bug = bool(r.get("uncategorized_is_bug", False))
        self.require_priority = r.get("require_priority", "auto")
        self.issue_types = {k.lower(): v for k, v in (r.get("issue_types") or {}).items()}
        self.project_context = r.get("project_context", "")
        self.weights = _deep_merge(DEFAULT_WEIGHTS, r.get("weights") or {})

        self.kind_rules = []
        for rule in r.get("kind_rules", []):
            kind = rule.get("kind")
            if kind not in KINDS:
                raise ProfileError(f"kind_rules: unknown kind '{kind}' (use one of {', '.join(KINDS)})")
            self.kind_rules.append((kind, self._compile(rule.get("labels", []), "kind_rules")))
        for kind in self.issue_types.values():
            if kind not in KINDS:
                raise ProfileError(f"issue_types: unknown kind '{kind}'")

        pri = r.get("priority") or {}
        self.priority_rules = [(p, self._compile(pri.get(p, []), f"priority.{p}")) for p in PRIORITIES]

        self.severity_rules = []
        for rule in r.get("severity_rules", []):
            lvl = rule.get("level")
            if lvl not in SEVERITIES:
                raise ProfileError(f"severity_rules: unknown level '{lvl}'")
            self.severity_rules.append((lvl, self._compile(rule.get("labels", []), "severity_rules"),
                                        self._compile(rule.get("all_of", []), "severity_rules")))

        sig = r.get("signals") or {}
        self.signal_rules = {s: self._compile(sig.get(s, []), f"signals.{s}") for s in SIGNALS}

        tri = r.get("triage") or {}
        self.team_re = self._compile_one(tri.get("team") or "", "triage.team")
        if self.team_re is not None and self.team_re.groups < 1:
            raise ProfileError("triage.team must contain one capture group for the team name")
        self.triaged_template = tri.get("triaged") or ""

        rel = (r.get("release") or {}).get("pattern") or ""
        self.release_re = self._compile_one(rel, "release.pattern")
        if self.release_re is not None and self.release_re.groups < 2:
            raise ProfileError("release.pattern needs two capture groups (major, minor)")
        self.dedupe_skip_kinds = set((r.get("dedupe") or {}).get("skip_kinds", ["internal"]))

    # -- regex helpers
    def _compile_one(self, pattern: str, where: str):
        if not pattern:
            return None
        try:
            return re.compile(pattern, self.flags)
        except re.error as e:
            raise ProfileError(f"{where}: bad regex {pattern!r}: {e}") from e

    def _compile(self, patterns, where: str):
        if isinstance(patterns, str):
            patterns = [patterns]
        return [self._compile_one(p, where) for p in patterns if p]

    @staticmethod
    def _any(regexes, labels) -> str | None:
        for rx in regexes:
            for label in labels:
                if rx.fullmatch(label):
                    return label
        return None

    # -- interpretation
    def kind(self, labels: list[str], issue_type: str | None = None) -> str:
        for kind, rxs in self.kind_rules:
            if self._any(rxs, labels):
                return kind
        if issue_type and issue_type.lower() in self.issue_types:
            return self.issue_types[issue_type.lower()]
        return "uncategorized"

    def priority(self, labels: list[str]) -> str | None:
        for p, rxs in self.priority_rules:
            if self._any(rxs, labels):
                return p
        return None

    def severity(self, labels: list[str]) -> str:
        best = "unrated"
        for lvl, any_rx, all_rx in self.severity_rules:
            hit = bool(all_rx) and all(self._any([rx], labels) for rx in all_rx)
            hit = hit or (bool(any_rx) and self._any(any_rx, labels) is not None)
            if hit and SEV_ORDER.index(lvl) > SEV_ORDER.index(best):
                best = lvl
        return best

    def has(self, signal: str, labels: list[str]) -> bool:
        return self._any(self.signal_rules.get(signal, []), labels) is not None

    def team(self, labels: list[str]) -> str | None:
        if self.team_re is None:
            return None
        for label in labels:
            m = self.team_re.fullmatch(label)
            if m:
                return m.group(m.lastindex or 1).strip()
        return None

    def release(self, labels: list[str]):
        if self.release_re is None:
            return None
        versions = []
        for label in labels:
            m = self.release_re.fullmatch(label)
            if m:
                try:
                    versions.append((int(m.group(1)), int(m.group(2))))
                except (TypeError, ValueError):
                    pass
        return max(versions) if versions else None

    def is_team_triaged(self, labels: list[str], team: str) -> bool:
        if not self.triaged_template:
            return True
        pattern = self.triaged_template.replace("{team}", re.escape(team))
        rx = re.compile(pattern, self.flags)
        return any(rx.fullmatch(l) for l in labels)

    def triage_status(self, labels: list[str], assignees: list[str], priority: str | None,
                      kind: str, uses_priorities: bool) -> str:
        if self.has("waiting", labels):
            return "Waiting for reporter"
        if self.has("needs_triage", labels):
            return "Needs triage"
        if self.triaged_template:  # two-step process (e.g. Flutter): route to a team, then the team triages
            team = self.team(labels)
            if team is None:
                return "Needs primary triage"
            if not self.is_team_triaged(labels, team):
                return "Needs team triage"
        elif not labels and not assignees:
            return "Needs triage"
        need_pri = self.require_priority is True or (self.require_priority == "auto" and uses_priorities)
        if need_pri and priority is None and (self.require_priority is True or kind == "bug"):
            return "Triaged, no priority"
        return "Triaged"

    def describe_label(self, label: str) -> str:
        """Human-readable summary of how one label is interpreted (for the Labels sheet)."""
        uses = []
        for kind, rxs in self.kind_rules:
            if self._any(rxs, [label]):
                uses.append(f"kind: {kind}")
                break
        p = self.priority([label])
        if p:
            uses.append(f"priority: {p}")
        for lvl, any_rx, all_rx in self.severity_rules:
            if self._any(any_rx, [label]):
                uses.append(f"severity: {lvl}")
                break
            if self._any(all_rx, [label]):
                uses.append(f"severity: {lvl} (with other labels)")
                break
        for s in SIGNALS:
            if self.has(s, [label]):
                uses.append(f"signal: {s.replace('_', ' ')}")
        t = self.team([label])
        if t:
            uses.append(f"team/area: {t}")
        if self.triaged_template and re.fullmatch(
                self.triaged_template.replace("{team}", ".+"), label, self.flags):
            uses.append("triaged marker")
        if self.release([label]):
            uses.append("found-in release")
        return "; ".join(uses)
