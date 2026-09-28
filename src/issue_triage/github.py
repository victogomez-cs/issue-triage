"""Stage 1: download every open issue of a repository through GitHub's GraphQL API."""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import time
from pathlib import Path

from .repo import Repo
from .util import log

GQL_QUERY = """
query($owner: String!, $name: String!, $cursor: String, $pageSize: Int!) {
  repository(owner: $owner, name: $name) {
    hasIssuesEnabled
    issues(first: $pageSize, after: $cursor, states: OPEN,
           orderBy: {field: CREATED_AT, direction: ASC}) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        number title body url createdAt updatedAt
        author { login }
        labels(first: 60) { nodes { name } }
        assignees(first: 10) { nodes { login } }
        milestone { title }
        __ISSUE_TYPE__
        comments { totalCount }
        reactions(content: THUMBS_UP) { totalCount }
      }
    }
  }
  rateLimit { remaining resetAt cost }
}
"""


class ExportError(RuntimeError):
    pass


class TransientError(Exception):
    pass


def find_github_token(host: str = "github.com") -> str | None:
    for var in ("GITHUB_TOKEN", "GH_TOKEN") + (("GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")
                                               if host != "github.com" else ()):
        if os.environ.get(var, "").strip():
            return os.environ[var].strip()
    try:
        out = subprocess.run(["gh", "auth", "token", "--hostname", host],
                             capture_output=True, text=True, timeout=15)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _gql(session, url: str, token: str, query: str, variables: dict, sleep=time.sleep) -> dict:
    import requests
    for attempt in range(8):
        try:
            r = session.post(url, json={"query": query, "variables": variables},
                             headers={"Authorization": f"bearer {token}"}, timeout=90)
        except requests.RequestException as e:
            log(f"  network error ({e}); retrying")
            sleep(min(60, 2 ** attempt))
            continue
        if r.status_code in (502, 503, 504):
            raise TransientError(f"HTTP {r.status_code}")
        if r.status_code == 401:
            raise ExportError("GitHub rejected the token (HTTP 401). Check GITHUB_TOKEN or run `gh auth login`.")
        if r.status_code in (403, 429):
            reset = r.headers.get("x-ratelimit-reset")
            wait = max(60, int(reset) - int(time.time()) + 5) if reset else 60
            log(f"  rate limited by GitHub; waiting {wait}s")
            sleep(min(wait, 3700))
            continue
        r.raise_for_status()
        payload = r.json()
        if payload.get("errors"):
            msg = json.dumps(payload["errors"])[:600]
            if "Could not resolve to a Repository" in msg or '"NOT_FOUND"' in msg:
                raise ExportError(f"Repository {variables['owner']}/{variables['name']} not found, or your "
                                  "token can't see it (private repos need a token with access).")
            if "timeout" in msg.lower() or "something went wrong" in msg.lower():
                raise TransientError(msg)
            raise RuntimeError(msg)
        return payload["data"]
    raise TransientError("too many retries")


def _normalize(n: dict) -> dict:
    return {
        "number": n["number"],
        "title": n["title"],
        "body": n.get("body") or "",
        "url": n["url"],
        "created_at": n["createdAt"],
        "updated_at": n["updatedAt"],
        "author": (n.get("author") or {}).get("login"),
        "labels": [l["name"] for l in n["labels"]["nodes"]],
        "assignees": [a["login"] for a in n["assignees"]["nodes"]],
        "milestone": (n.get("milestone") or {}).get("title"),
        "issue_type": (n.get("issueType") or {}).get("name") if n.get("issueType") else None,
        "comments": n["comments"]["totalCount"],
        "thumbs_up": n["reactions"]["totalCount"],
    }


def export_issues(repo: Repo, out_dir: Path, token: str | None = None, page_size: int = 50,
                  fresh: bool = False, session=None, sleep=time.sleep) -> Path:
    """Download all open issues to out_dir/issues.json, resuming a previous partial export."""
    import requests
    token = token or find_github_token(repo.host)
    if not token:
        raise ExportError("No GitHub token found. Set GITHUB_TOKEN (read access to the repo is enough) "
                          "or log in with `gh auth login`.")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path, partial, ckpt = out_dir / "issues.json", out_dir / "issues.partial.jsonl", out_dir / "export_checkpoint.json"
    session = session or requests.Session()
    if fresh:
        for p in (partial, ckpt):
            p.unlink(missing_ok=True)
    cursor = json.loads(ckpt.read_text())["cursor"] if ckpt.exists() and partial.exists() else None
    if cursor:
        log(f"Resuming export ({sum(1 for _ in partial.open())} issues already saved)")
    else:
        partial.unlink(missing_ok=True)

    size, include_type = page_size, True
    with partial.open("a", encoding="utf-8") as f:
        while True:
            q = GQL_QUERY.replace("__ISSUE_TYPE__", "issueType { name }" if include_type else "")
            try:
                data = _gql(session, repo.graphql_url, token, q,
                            {"owner": repo.owner, "name": repo.name, "cursor": cursor, "pageSize": size}, sleep)
            except TransientError as e:
                size = max(10, size // 2)
                log(f"  GitHub struggled ({e}); retrying with {size} issues per page")
                sleep(5)
                continue
            except RuntimeError as e:
                if include_type and "issueType" in str(e):   # older GitHub Enterprise versions
                    include_type = False
                    continue
                raise
            repository = data.get("repository")
            if repository is None:
                raise ExportError(f"Repository {repo.slug} not found or not visible to your token.")
            if repository.get("hasIssuesEnabled") is False:
                raise ExportError(f"{repo.slug} has GitHub Issues turned off.")
            issues = repository["issues"]
            for node in issues["nodes"]:
                f.write(json.dumps(_normalize(node)) + "\n")
            f.flush()
            cursor = issues["pageInfo"]["endCursor"]
            ckpt.write_text(json.dumps({"cursor": cursor}))
            done = sum(1 for _ in partial.open())
            log(f"  {done:,}/{issues['totalCount']:,} issues")
            if size < page_size:
                size = min(page_size, size * 2)
            if not issues["pageInfo"]["hasNextPage"]:
                break

    by_number = {}
    for line in partial.open(encoding="utf-8"):
        rec = json.loads(line)
        by_number[rec["number"]] = rec
    out_path.write_text(json.dumps({
        "repo": repo.slug,
        "host": repo.host,
        "exported_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "issues": sorted(by_number.values(), key=lambda i: i["number"]),
    }))
    partial.unlink(missing_ok=True)
    ckpt.unlink(missing_ok=True)
    log(f"Saved {len(by_number):,} open issues")
    return out_path


def load_issues(path: Path) -> dict:
    if not path.exists():
        raise ExportError(f"{path} not found. Run the export first.")
    blob = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(blob, list):  # a bare list of issues is fine too
        blob = {"repo": None, "exported_at": None, "issues": blob}
    return blob
