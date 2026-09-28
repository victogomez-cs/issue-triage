"""Issue-body cleanup shared by the AI and duplicate stages."""

from __future__ import annotations

import re

_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_DETAILS_RE = re.compile(r"<details>(.*?)</details>", re.S | re.I)
_FENCE_RE = re.compile(r"```.*?```", re.S)
_URL_RE = re.compile(r"https?://\S+")


def clean_body(body: str | None, max_chars: int = 4000) -> str:
    """Strip template comments and shrink logs, keeping the start of each (stack traces matter)."""
    body = _COMMENT_RE.sub("", body or "")
    body = _DETAILS_RE.sub(lambda m: "<details>" + m.group(1)[:700] + " …</details>", body)

    def shrink_fence(m):
        lines = m.group(0).splitlines()
        return "\n".join(lines[:16] + (["… (truncated)", "```"] if len(lines) > 16 else []))

    body = _FENCE_RE.sub(shrink_fence, body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body[:max_chars] + (" …(truncated)" if len(body) > max_chars else "")


def dedupe_body(body: str | None) -> str:
    """Prose only: logs, code, template comments and links removed."""
    body = _FENCE_RE.sub(" ", _DETAILS_RE.sub(" ", _COMMENT_RE.sub(" ", body or "")))
    return _URL_RE.sub(" ", body)[:1500]
