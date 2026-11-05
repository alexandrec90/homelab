"""`.env`: the one secrets file, shared by every machine and never committed."""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable

SECRET_NAME = re.compile(r"PASSWORD|SECRET")
_ASSIGNMENT = re.compile(r"^(?P<name>[A-Za-z_]\w*)=(?P<value>.*)$")


def parse_env(text: str) -> dict[str, str]:
    """Assignments in a dotenv file; comments and blank lines are not."""
    found = {}
    for line in text.splitlines():
        match = _ASSIGNMENT.match(line.strip())
        if match:
            found[match["name"]] = match["value"].strip().strip("\"'")
    return found


def new_secret() -> str:
    return secrets.token_urlsafe(24)


def _filled(line: str, make_secret: Callable[[], str]) -> str:
    match = _ASSIGNMENT.match(line)
    if match and not match["value"] and SECRET_NAME.search(match["name"]):
        return f"{match['name']}={make_secret()}"
    return line


def merge_example(
    example: str, existing: str = "", make_secret: Callable[[], str] = new_secret
) -> str:
    """`.env` with every key of the example present and each blank secret generated.

    Values already set are kept, so this is safe to run again after a new stack adds keys.
    Tokens that come from another service's UI have names the pattern does not match, and
    stay blank for a person to fill in.
    """
    if not existing.strip():
        return "\n".join(_filled(line, make_secret) for line in example.splitlines()) + "\n"
    have = parse_env(existing)
    lines = [_filled(line, make_secret) for line in existing.splitlines()]
    added = [
        _filled(line, make_secret)
        for line in example.splitlines()
        if (match := _ASSIGNMENT.match(line)) and match["name"] not in have
    ]
    return "\n".join([*lines, *added]) + "\n"
