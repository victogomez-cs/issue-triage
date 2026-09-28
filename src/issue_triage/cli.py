"""Command-line interface.

    issue-triage https://github.com/owner/repo        # the whole process
    issue-triage                                       # asks for the repository
    issue-triage <command> owner/repo [options]        # one stage at a time
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

from . import __version__
from . import ai as ai_mod
from . import dedupe as dedupe_mod
from .github import ExportError, export_issues, find_github_token, load_issues
from .profile import ProfileError, builtin_profiles, load_profile
from .repo import Repo, parse_repo
from .report import write_report
from .util import confirm, is_interactive, log

COMMANDS = ("run", "export", "classify", "duplicates", "report", "import-ai", "labels", "profiles")


class Workspace:
    """Where one repository's data and report live: <out>/<owner>__<name>/."""

    def __init__(self, repo: Repo, out: str):
        self.repo = repo
        self.dir = Path(out) / repo.dir_name
        self.issues = self.dir / "issues.json"
        self.report = self.dir / f"{repo.owner}__{repo.name}_triage.xlsx"


def _profile(args, repo: Repo):
    return load_profile(args.profile, repo.slug, args.config)


def _blob(ws: Workspace) -> dict:
    blob = load_issues(ws.issues)
    blob.setdefault("repo", ws.repo.slug)
    blob.setdefault("host", ws.repo.host)
    return blob


def _age(path: Path) -> str:
    hours = (dt.datetime.now().timestamp() - path.stat().st_mtime) / 3600
    return f"{hours:.0f} hours" if hours < 48 else f"{hours / 24:.0f} days"


# ---------------------------------------------------------------- stages

def do_export(args, ws: Workspace, fresh: bool = False) -> None:
    log(f"Exporting open issues from {ws.repo.web_url}")
    export_issues(ws.repo, ws.dir, page_size=args.page_size, fresh=fresh)


def do_classify(args, ws: Workspace, profile) -> dict:
    blob = _blob(ws)
    return ai_mod.classify(blob["issues"], ws.dir, profile, ws.repo.slug, model=args.model, scope=args.scope,
                           mode=args.ai_mode, limit=args.limit, workers=args.workers, wait=not args.no_wait,
                           dry_run=getattr(args, "dry_run", False), assume_yes=args.yes)


def do_duplicates(args, ws: Workspace, profile) -> None:
    blob = _blob(ws)
    res = dedupe_mod.find_duplicates(blob["issues"], profile, threshold=args.threshold, embeddings=args.embeddings)
    dedupe_mod.save(res, ws.dir)
    log(f"Found {len(res['pairs']):,} possible duplicate pairs ({res['method']})")


def do_report(args, ws: Workspace, profile) -> dict:
    blob = _blob(ws)
    out = Path(args.output) if getattr(args, "output", None) else ws.report
    summary = write_report(blob, profile, ai_mod.load_results(ws.dir), dedupe_mod.load(ws.dir), out)
    u = summary["urgency"]
    log("")
    log(f"Report: {summary['xlsx']}")
    log(f"  {summary['issues']:,} open issues, {summary['bugs']:,} bugs "
        f"(urgency: {u['Critical']:,} critical, {u['High']:,} high, {u['Medium']:,} medium, {u['Low']:,} low)")
    log(f"  {summary['review']:,} issues in the review queue, {summary['dup_pairs']:,} possible duplicate pairs")
    if summary["uncategorized"]:
        log(f"  {summary['uncategorized']:,} issues have no kind label. Set ANTHROPIC_API_KEY and run again to "
            "classify them with AI, or add label rules to a triage.toml (see the Labels sheet).")
    return summary


# ---------------------------------------------------------------- commands

def cmd_run(args) -> None:
    repo = _resolve_repo(args)
    ws = Workspace(repo, args.out)
    profile = _profile(args, repo)
    log(f"Label profile: {profile.source}")

    if args.refresh or not ws.issues.exists() or (ws.dir / "export_checkpoint.json").exists():
        do_export(args, ws, fresh=args.refresh)
    else:
        log(f"Using the export from {_age(ws.issues)} ago (add --refresh for fresh data)")

    if args.ai_results:
        n = ai_mod.import_results(Path(args.ai_results), ws.dir)
        log(f"Imported {n:,} AI results from {args.ai_results}")

    want_ai = args.ai if args.ai is not None else bool(os.environ.get("ANTHROPIC_API_KEY"))
    if want_ai:
        try:
            s = do_classify(args, ws, profile)
            if s.get("pending_batches"):
                log("The report below uses the AI results collected so far.")
        except ai_mod.AIError as e:
            log(f"AI classification skipped: {e}")
    elif not ai_mod.load_results(ws.dir):
        log("AI classification off (no ANTHROPIC_API_KEY). Scoring uses labels only.")

    do_duplicates(args, ws, profile)
    do_report(args, ws, profile)


def cmd_export(args) -> None:
    repo = _resolve_repo(args)
    do_export(args, Workspace(repo, args.out), fresh=args.fresh)


def cmd_classify(args) -> None:
    repo = _resolve_repo(args)
    ws = Workspace(repo, args.out)
    s = do_classify(args, ws, _profile(args, repo))
    if args.dry_run:
        log(f"{s['to_classify']:,} issues would be classified (~{s['approx_input_tokens'] / 1e6:.1f}M input tokens); "
            f"{s['cached']:,} already have results.")


def cmd_duplicates(args) -> None:
    repo = _resolve_repo(args)
    ws = Workspace(repo, args.out)
    do_duplicates(args, ws, _profile(args, repo))


def cmd_report(args) -> None:
    repo = _resolve_repo(args)
    ws = Workspace(repo, args.out)
    do_report(args, ws, _profile(args, repo))


def cmd_import_ai(args) -> None:
    repo = _resolve_repo(args)
    ws = Workspace(repo, args.out)
    n = ai_mod.import_results(Path(args.file), ws.dir)
    log(f"Imported {n:,} AI results into {ws.dir}. Run `issue-triage report {repo.slug}` to use them.")


def cmd_labels(args) -> None:
    from collections import Counter
    repo = _resolve_repo(args)
    ws = Workspace(repo, args.out)
    profile = _profile(args, repo)
    counts = Counter(l for i in _blob(ws)["issues"] for l in i["labels"])
    width = min(45, max((len(l) for l in counts), default=10))
    print(f"Label profile: {profile.source}\n")
    for label, n in counts.most_common(args.top):
        print(f"{label[:width]:<{width}}  {n:>6}  {profile.describe_label(label) or '-'}")


def cmd_profiles(args) -> None:
    for name in builtin_profiles():
        p = load_profile(name)
        print(f"{name:<10} {p.raw.get('description', '')}")


def _resolve_repo(args) -> Repo:
    if not getattr(args, "repo", None):
        if not is_interactive():
            raise SystemExit("Give a repository, like: issue-triage https://github.com/owner/name")
        args.repo = input("GitHub repository (URL or owner/name): ").strip()
    return parse_repo(args.repo)


def interactive() -> None:
    """No arguments on a terminal: ask for the repository and walk through the run."""
    print(f"issue-triage {__version__}: rank a repository's open issues.\n")
    while True:
        try:
            repo = parse_repo(input("GitHub repository (URL or owner/name): "))
            break
        except ValueError as e:
            print(e)
    if not find_github_token(repo.host):
        print("\nA GitHub token is needed to read issues. Either:\n"
              "  • run `gh auth login` (GitHub CLI), or\n"
              "  • set GITHUB_TOKEN to a personal access token with read access to the repository.")
        raise SystemExit(1)
    use_ai = False
    if os.environ.get("ANTHROPIC_API_KEY"):
        use_ai = confirm("ANTHROPIC_API_KEY is set. Classify issues with AI?", default=True)
    else:
        print("No ANTHROPIC_API_KEY set: issues will be scored from their labels only.")
    argv = ["run", f"{repo.web_url}", "--ai" if use_ai else "--no-ai"]
    main(argv)


# ---------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="issue-triage",
        description="Rank a GitHub repository's open issues: kind, severity, duplicates, and what to fix first.",
        epilog="Run `issue-triage <command> -h` for a command's options. "
               "Shortcut: `issue-triage <repo>` is the same as `issue-triage run <repo>`.")
    p.add_argument("--version", action="version", version=f"issue-triage {__version__}")
    sub = p.add_subparsers(dest="cmd", metavar="command")

    def common(sp, repo=True):
        if repo:
            sp.add_argument("repo", nargs="?", help="owner/name or a GitHub URL (asked for if omitted)")
        sp.add_argument("--out", default="triage-output", help="folder for data and reports (default: %(default)s)")
        sp.add_argument("--profile", default="auto",
                        help=f"label profile: auto, {', '.join(builtin_profiles())} (default: auto)")
        sp.add_argument("--config", help="a triage.toml with your own label rules or weights")

    def export_opts(sp):
        sp.add_argument("--page-size", type=int, default=50, help=argparse.SUPPRESS)

    def ai_opts(sp):
        sp.add_argument("--model", default=ai_mod.DEFAULT_MODEL, help="Claude model (default: %(default)s)")
        sp.add_argument("--scope", choices=["all", "bugs", "uncategorized"], default="all",
                        help="which issues the AI reads: all (default), bugs = bug-labeled + unlabeled, "
                             "uncategorized = only issues with no kind label")
        sp.add_argument("--ai-mode", choices=["auto", "batch", "sync"], default="auto",
                        help="batch = Message Batches API (cheaper, slower); sync = immediate; "
                             f"auto = sync up to {ai_mod.AUTO_SYNC_MAX} issues (default)")
        sp.add_argument("--limit", type=int, help="classify at most this many issues (good for a trial)")
        sp.add_argument("--workers", type=int, default=4, help="parallel requests in sync mode")
        sp.add_argument("--no-wait", action="store_true", help="submit batches and exit; run again to collect")
        sp.add_argument("-y", "--yes", action="store_true", help="don't ask before spending API credits")

    def dup_opts(sp):
        sp.add_argument("--embeddings", action="store_true",
                        help="use a local embedding model for duplicates (needs issue-triage[embeddings])")
        sp.add_argument("--threshold", type=float, help="similarity cutoff for possible duplicates")

    sp = sub.add_parser("run", help="the whole process: export, AI (if ANTHROPIC_API_KEY is set), duplicates, report")
    common(sp)
    export_opts(sp)
    ai_opts(sp)
    dup_opts(sp)
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--ai", dest="ai", action="store_true", default=None, help="classify with AI (default if a key is set)")
    g.add_argument("--no-ai", dest="ai", action="store_false", help="skip AI classification")
    sp.add_argument("--refresh", action="store_true", help="download issues again even if an export exists")
    sp.add_argument("--ai-results", help="import AI results from a .json/.jsonl file before the report")
    sp.add_argument("--output", help="write the .xlsx here instead of the data folder")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("export", help="download all open issues")
    common(sp)
    export_opts(sp)
    sp.add_argument("--fresh", action="store_true", help="start over instead of resuming a partial export")
    sp.set_defaults(func=cmd_export)

    sp = sub.add_parser("classify", help="classify issues with Claude (needs ANTHROPIC_API_KEY)")
    common(sp)
    ai_opts(sp)
    sp.add_argument("--dry-run", action="store_true", help="only count issues and estimate tokens")
    sp.set_defaults(func=cmd_classify)

    sp = sub.add_parser("duplicates", help="find possible duplicate issues")
    common(sp)
    dup_opts(sp)
    sp.set_defaults(func=cmd_duplicates)

    sp = sub.add_parser("report", help="score issues and write the Excel report")
    common(sp)
    sp.add_argument("--output", help="write the .xlsx here instead of the data folder")
    sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("import-ai", help="import AI results produced elsewhere (.json or .jsonl)")
    common(sp)
    sp.add_argument("file", help="the results file")
    sp.set_defaults(func=cmd_import_ai)

    sp = sub.add_parser("labels", help="show how each label in an exported repo is interpreted")
    common(sp)
    sp.add_argument("--top", type=int, default=60, help="how many labels to show (default: %(default)s)")
    sp.set_defaults(func=cmd_labels)

    sp = sub.add_parser("profiles", help="list built-in label profiles")
    sp.set_defaults(func=cmd_profiles)
    return p


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        if is_interactive():
            return interactive()
        build_parser().print_help()
        raise SystemExit(2)
    if argv[0] not in COMMANDS and not argv[0].startswith("-"):
        argv = ["run"] + argv  # `issue-triage <repo>` shortcut
    args = build_parser().parse_args(argv)
    if not getattr(args, "func", None):
        build_parser().print_help()
        raise SystemExit(2)
    try:
        args.func(args)
    except (ExportError, ProfileError, ai_mod.AIError, ValueError) as e:
        log(f"Error: {e}")
        raise SystemExit(1)
    except KeyboardInterrupt:
        log("\nStopped. Run the same command again to pick up where it left off.")
        raise SystemExit(130)


if __name__ == "__main__":
    main()
