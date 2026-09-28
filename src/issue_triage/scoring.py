"""Turn labels, reactions and (optional) AI results into kind, severity, urgency and review flags."""

from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass

from .ai import AI_KINDS
from .profile import SEV_ORDER, Profile

KIND_DISPLAY = {
    "bug": "Bug",
    "bug_assumed": "Bug (assumed)",
    "feature": "Feature / proposal",
    "internal": "Internal (tech debt, infra, flake)",
    "docs": "Docs",
    "question": "Question / support",
    "not_actionable": "Not actionable",
    "uncategorized": "Uncategorized",
}
BUG_KINDS = {"Bug", "Bug (assumed)"}
USES_PRIORITIES_SHARE = 0.2  # require_priority = "auto" applies once this share of issues has a priority label
URGENCY_TIERS = ("Critical", "High", "Medium", "Low")

# Without AI, an unlabeled issue whose title clearly describes a crash/hang is rated "high".
CRASH_TITLE_RE = re.compile(r"\b(crash(es|ed|ing)?|segfault|sigsegv|sigabrt|exc_bad_access|fatal (error|exception)"
                            r"|anr|freez(e|es|ing)|hangs?|deadlock|data loss)\b", re.I)


def parse_ts(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Context:
    profile: Profile
    now: dt.datetime
    recent_releases: set
    uses_priorities: bool


def build_context(issues: list[dict], profile: Profile, exported_at: str | None) -> Context:
    now = parse_ts(exported_at) if exported_at else dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    releases = sorted({r for i in issues if (r := profile.release(i["labels"]))}, reverse=True)
    n_pri = sum(1 for i in issues if profile.priority(i["labels"]))
    return Context(profile, now, set(releases[: int(profile.weights["recent_release_count"])]),
                   uses_priorities=bool(issues) and n_pri / len(issues) >= USES_PRIORITIES_SHARE)


def score_issue(issue: dict, ai: dict | None, ctx: Context, dup_of: tuple | None = None) -> dict:
    p, w = ctx.profile, ctx.profile.weights
    labels = issue["labels"]
    priority = p.priority(labels)
    lk = p.kind(labels, issue.get("issue_type"))
    ai_ok = bool(ai) and ai.get("kind") in AI_KINDS
    ai_conf = float(ai.get("confidence", 0) or 0) if ai_ok else 0.0
    ai_kind = AI_KINDS[ai["kind"]] if ai_ok else None
    flags = []

    # Kind: human labels win; the AI fills gaps and flags disagreements.
    if lk != "uncategorized":
        kind = lk
        if ai_kind and ai_conf >= w["ai_min_confidence_flag"] and ai_kind != lk:
            if lk == "feature" and ai_kind == "bug":
                flags.append("AI: looks like a bug, labeled feature")
            elif lk == "bug":
                flags.append(f"AI: may not be a bug ({KIND_DISPLAY[ai_kind]})")
    elif ai_kind and ai_conf >= w["ai_min_confidence_fill"]:
        kind = ai_kind
    else:
        kind = "bug_assumed" if p.uncategorized_is_bug else "uncategorized"
    is_bug = kind in ("bug", "bug_assumed")

    # Severity: the worse of label-derived and AI-derived.
    sev = p.severity(labels)
    ai_sev = ai.get("severity") if ai_ok else None
    if ai_sev in SEV_ORDER and SEV_ORDER.index(ai_sev) > SEV_ORDER.index(sev):
        sev = ai_sev
    keyword_sev = sev == "unrated" and not ai_ok and bool(CRASH_TITLE_RE.search(issue["title"]))
    if keyword_sev:
        sev = "high"
    sev_display = ("Unrated" if sev == "unrated" else sev.capitalize()) if is_bug else "n/a"

    # Score
    parts = []

    def add(name, pts):
        if pts:
            parts.append((name, pts))

    sig = w["signals"]
    add(f"priority {priority or 'none'}", w["priority"][priority or "none"])
    if is_bug:
        add(f"severity {sev}" + (" (title keyword)" if keyword_sev else ""), w["severity"][sev])
    if p.has("production", labels):
        add("production", sig["production"])
    elif ai_ok and ai.get("affects_production"):
        add("AI: production", sig["ai_production"])
    if p.has("named_customer", labels):
        add("named customer", sig["named_customer"])
    elif p.has("crowd_customer", labels):
        add("customer: crowd", sig["crowd_customer"])
    has_repro_label = p.has("reproducible", labels)
    if has_repro_label:
        add("reproducible", sig["reproducible"])
    elif ai_ok and ai.get("has_reproduction"):
        add("AI: reproducible", sig["ai_reproducible"])
    needs_info = p.has("needs_info", labels)
    if needs_info:
        add("needs repro info", sig["needs_info"])
    if p.has("waiting", labels):
        add("waiting for response", sig["waiting"])
    thumbs = int(issue.get("thumbs_up") or 0)
    add(f"{thumbs} thumbs-up", round(min(float(w["thumbs_up_cap"]), w["thumbs_up_scale"] * math.log2(1 + thumbs)), 1))
    rel = p.release(labels)
    if rel and rel in ctx.recent_releases:
        add("recent release", sig["recent_release"])
    days_since_update = (ctx.now - parse_ts(issue["updated_at"])).days
    if days_since_update <= w["recent_days"]:
        add("recently active", sig["recently_active"])
    elif days_since_update > w["stale_days"]:
        add("stale", sig["stale"])
    score = round(sum(pts for _, pts in parts), 1)

    urgency = "Low"
    for tier in ("Critical", "High", "Medium"):
        if score >= w["urgency_thresholds"][tier]:
            urgency = tier
            break
    floor = w["urgency_floors"].get(priority or "")
    order = list(URGENCY_TIERS)[::-1]
    if floor in order and order.index(floor) > order.index(urgency):
        urgency = floor
    if not is_bug:
        urgency = "n/a"

    # Review flags
    status = p.triage_status(labels, issue.get("assignees") or [], priority, lk, ctx.uses_priorities)
    if status in ("Needs triage", "Needs primary triage", "Needs team triage", "Triaged, no priority"):
        flags.append(status)
    if priority == "P3" and p.has("crash", labels):
        flags.append("Crash at P3")
    if priority == "P3" and p.has("regression", labels):
        flags.append("Regression at P3")
    if priority == "P3" and thumbs >= w["popular_thumbs_up"]:
        flags.append(f"Popular ({thumbs} thumbs-up) at P3")
    if ai_ok and ai_sev == "critical" and priority in ("P2", "P3") and is_bug and ai_conf >= w["ai_min_confidence_flag"]:
        flags.append(f"AI: critical but {priority}")
    if dup_of:
        flags.append("Possible duplicate")
    if days_since_update > w["stale_days"]:
        flags.append("Stale 2+ years")

    return {
        "priority": priority, "label_kind": lk, "kind": KIND_DISPLAY[kind], "severity": sev_display,
        "score": score, "urgency": urgency, "score_breakdown": ", ".join(f"{n} {v:+g}" for n, v in parts),
        "team": p.team(labels), "triage_status": status, "flags": "; ".join(flags),
        "release": f"{rel[0]}.{rel[1]}" if rel else None, "days_since_update": days_since_update,
        "ai_kind": ai["kind"] if ai_ok else None, "ai_severity": ai_sev,
        "ai_confidence": round(ai_conf, 2) if ai_ok else None,
        "ai_summary": ai.get("summary") if ai_ok else None,
        "has_repro": "Yes" if has_repro_label else (
            "AI: yes" if ai_ok and ai.get("has_reproduction") else ("Needs info" if needs_info else "")),
        "dup_of": dup_of[0] if dup_of else None, "dup_sim": dup_of[1] if dup_of else None,
    }
