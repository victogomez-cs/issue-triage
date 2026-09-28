"""Parse the repository argument: owner/name, a GitHub URL, a git remote, or a GitHub Enterprise URL."""

from __future__ import annotations

import re
from dataclasses import dataclass

_NAME = r"[A-Za-z0-9_.-]+"


@dataclass(frozen=True)
class Repo:
    owner: str
    name: str
    host: str = "github.com"

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def graphql_url(self) -> str:
        return "https://api.github.com/graphql" if self.host == "github.com" else f"https://{self.host}/api/graphql"

    @property
    def web_url(self) -> str:
        return f"https://{self.host}/{self.slug}"

    @property
    def dir_name(self) -> str:
        prefix = "" if self.host == "github.com" else self.host.replace(":", "_") + "__"
        return f"{prefix}{self.owner}__{self.name}"


def parse_repo(text: str) -> Repo:
    s = (text or "").strip()
    if not s:
        raise ValueError("Enter a repository, like owner/name or https://github.com/owner/name")
    m = re.fullmatch(rf"git@([^:/\s]+):({_NAME})/({_NAME})/?", s)
    if m:
        return _mk(m.group(2), m.group(3), m.group(1))
    m = re.fullmatch(rf"(?:(?:https?|ssh|git)://)?(?:[^@/\s]+@)?([^/\s]+\.[^/\s]+)/({_NAME})/({_NAME})(?:[/?#].*)?", s)
    if m:
        host = m.group(1).lower()
        if host in ("www.github.com", "api.github.com"):
            host = "github.com"
        return _mk(m.group(2), m.group(3), host)
    m = re.fullmatch(rf"({_NAME})/({_NAME})", s)
    if m:
        return _mk(m.group(1), m.group(2))
    raise ValueError(f"Couldn't read {text!r} as a repository. Use owner/name or https://github.com/owner/name")


def _mk(owner: str, name: str, host: str = "github.com") -> Repo:
    if name.endswith(".git"):
        name = name[:-4]
    if not owner or not name or name in (".", ".."):
        raise ValueError("Repository owner and name can't be empty")
    return Repo(owner=owner, name=name, host=host)
