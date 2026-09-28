"""Small shared helpers."""

from __future__ import annotations

import sys


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stderr.isatty()


def confirm(question: str, default: bool = False) -> bool:
    """Yes/no prompt; returns the default when not attached to a terminal."""
    if not is_interactive():
        return default
    suffix = " [Y/n] " if default else " [y/N] "
    while True:
        ans = input(question + suffix).strip().lower()
        if not ans:
            return default
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
