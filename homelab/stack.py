"""Static rules for `compose.yaml`: the mistakes that only show up on the host.

The stack is written on one machine and run on another, so a bad edit is otherwise found
by someone logged in to a server. `check_stack` turns each of these into a failing test
in the PR instead (`tests/test_stack.py`). It reads text and needs no Docker daemon.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

_VARIABLE = re.compile(r"\$\{(?P<name>[A-Za-z_]\w*)(?P<operator>:?[-?+])?(?P<argument>[^}]*)\}")
_ENV_KEY = re.compile(r"^(?P<name>[A-Za-z_]\w*)=", re.MULTILINE)
_SECRET_NAME = re.compile(r"PASSWORD|SECRET")
_FLOATING_TAGS = frozenset({"latest", "stable", "production", "edge", "nightly", "unstable"})


def referenced_vars(compose_text: str) -> dict[str, set[str]]:
    """Each `${VAR}` in the file, mapped to the operators it is used with."""
    found: dict[str, set[str]] = {}
    for match in _VARIABLE.finditer(compose_text):
        found.setdefault(match["name"], set()).add(match["operator"] or "")
    return found


def documented_vars(env_text: str) -> set[str]:
    """The keys `.env.example` assigns; a commented-out key does not count."""
    return {match["name"] for match in _ENV_KEY.finditer(env_text)}


def undocumented_vars(compose_text: str, env_text: str) -> list[str]:
    """Variables the stack reads that a person setting up a host would never hear of."""
    return sorted(set(referenced_vars(compose_text)) - documented_vars(env_text))


def unguarded_secrets(compose_text: str) -> list[str]:
    """Secrets not required with `:?`: blank ones would start the service with no password."""
    return sorted(
        name
        for name, operators in referenced_vars(compose_text).items()
        if _SECRET_NAME.search(name) and operators != {":?"}
    )


def floating_images(services: dict[str, Any]) -> list[str]:
    """Services whose image can change under a host with no commit to say so."""
    return sorted(
        f"{name}: {service.get('image')}"
        for name, service in services.items()
        if _image_floats(str(service.get("image", "")))
    )


def _image_floats(image: str) -> bool:
    if "@sha256:" in image:
        return False
    # The last path segment only: a registry host may carry a `:port` of its own.
    _, separator, tag = image.rpartition("/")[2].partition(":")
    return not separator or tag in _FLOATING_TAGS


def unprofiled_services(services: dict[str, Any]) -> list[str]:
    """Services no `COMPOSE_PROFILES` choice can turn off, which would start on every host."""
    return sorted(name for name, service in services.items() if not service.get("profiles"))


def host_port_clashes(services: dict[str, Any]) -> list[str]:
    """Host ports two services publish, compared at their defaults, per protocol."""
    owners: dict[tuple[str, str], set[str]] = {}
    for name, service in services.items():
        for port in service.get("ports", ()):
            owners.setdefault(_published(str(port)), set()).add(name)
    return sorted(
        f"{port}/{protocol}: {', '.join(sorted(names))}"
        for (port, protocol), names in owners.items()
        if len(names) > 1
    )


def _published(port: str) -> tuple[str, str]:
    """`("8096", "tcp")` from `"${JELLYFIN_HTTP_PORT:-8096}:8096"`."""
    resolved = _VARIABLE.sub(lambda match: match["argument"], port)
    mapping, _, protocol = resolved.partition("/")
    host = mapping.rsplit(":", 2)[-2] if ":" in mapping else mapping
    return host, protocol or "tcp"


def check_stack(compose_text: str, env_text: str) -> list[str]:
    """Every rule broken by this `compose.yaml` and `.env.example`; empty when clean."""
    services: dict[str, Any] = yaml.safe_load(compose_text).get("services") or {}
    rules = {
        "not in .env.example": undocumented_vars(compose_text, env_text),
        "secret not required with :?": unguarded_secrets(compose_text),
        "image not pinned": floating_images(services),
        "service has no profile": unprofiled_services(services),
        "host port published twice": host_port_clashes(services),
    }
    return [f"{rule}: {item}" for rule, items in rules.items() for item in items]
