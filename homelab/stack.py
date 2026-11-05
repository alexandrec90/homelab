"""Static rules for `stacks/`: the mistakes that only show up on a host.

The stacks are written on one machine and run on others, so a bad edit is otherwise found
by someone logged in to a server. `check_stacks` turns each of these into a failing test
in the PR instead (`tests/test_stack.py`). It reads text and needs no Docker daemon. It is
also the one module here with a third-party import, so `python -m homelab` never loads it.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

from homelab.envfile import SECRET_NAME
from homelab.fleet import VARIABLE, ImageLimit, Stack
from homelab.render import GENERATED_VARS

_ENV_KEY = re.compile(r"^(?P<name>[A-Za-z_]\w*)=", re.MULTILINE)
_VERSION = re.compile(r"^v?(\d+)\.(\d+)")
_FLOATING_TAGS = frozenset(
    {"latest", "stable", "production", "release", "main", "edge", "nightly", "unstable"}
)


def referenced_vars(compose_text: str) -> dict[str, set[str]]:
    """Each `${VAR}` in the file, mapped to the operators it is used with."""
    found: dict[str, set[str]] = {}
    for match in VARIABLE.finditer(compose_text):
        found.setdefault(match["name"], set()).add(match["operator"] or "")
    return found


def documented_vars(env_text: str) -> set[str]:
    """The keys `.env.example` assigns; a commented-out key does not count."""
    return {match["name"] for match in _ENV_KEY.finditer(env_text)}


def undocumented_vars(compose_text: str, env_text: str) -> list[str]:
    """Variables a stack reads that nobody setting up a host would ever hear of."""
    known = documented_vars(env_text) | set(GENERATED_VARS)
    return sorted(set(referenced_vars(compose_text)) - known)


def unguarded_secrets(compose_text: str) -> list[str]:
    """Secrets not required with `:?`: blank ones would start a service with no password."""
    return sorted(
        name
        for name, operators in referenced_vars(compose_text).items()
        if SECRET_NAME.search(name) and operators != {":?"}
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


def published_ports(services: dict[str, Any]) -> set[tuple[str, str]]:
    """Every `(host port, protocol)` these services publish, at their defaults."""
    return {_published(str(p)) for s in services.values() for p in s.get("ports", ())}


def _published(port: str) -> tuple[str, str]:
    """`("8096", "tcp")` from `"${JELLYFIN_PORT:-8096}:8096"`."""
    resolved = VARIABLE.sub(lambda match: match["argument"], port)
    mapping, _, protocol = resolved.partition("/")
    host = mapping.rsplit(":", 2)[-2] if ":" in mapping else mapping
    return host, protocol or "tcp"


def _version(text: str) -> tuple[int, int] | None:
    """`(11, 4)` from `11.4.13`, compared as numbers so `9.9` is below `10.11`."""
    match = _VERSION.match(text)
    return (int(match[1]), int(match[2])) if match else None


def _outside(image: str, limit: ImageLimit) -> list[str]:
    version = _version(image.rpartition("/")[2].partition(":")[2])
    if version is None:
        return [f"{image} has no version to check"]
    low, high = _version(limit.min), _version(limit.max)
    problems = []
    if low and version < low:
        problems.append(f"{image} is before {limit.min}")
    if high and version > high:
        problems.append(f"{image} is past {limit.max}")
    return problems


def image_limit_problems(stack: Stack, services: dict[str, Any]) -> list[str]:
    """Images outside the versions their stack works with.

    A limit that finds nothing to check is itself a finding, as is a tag with no version
    in it. Either would otherwise mean the guard had quietly stopped guarding.
    """
    images = {str(service.get("image", "")) for service in services.values()}
    problems: list[str] = []
    for limit in stack.image_limits:
        matching = sorted(i for i in images if _image_name(i) == limit.image)
        if not matching:
            problems.append(f"limit on {limit.image} matches no image")
        for image in matching:
            problems.extend(_outside(image, limit))
    return problems


def _image_name(image: str) -> str:
    """`mariadb` from `docker.io/library/mariadb:11.4.13`."""
    return image.rpartition("/")[2].partition(":")[0].partition("@")[0]


def uses_host_network(services: dict[str, Any]) -> bool:
    return any(service.get("network_mode") == "host" for service in services.values())


def _route_port_problems(stack: Stack, services: dict[str, Any]) -> list[str]:
    """A route the proxy would send to a port nothing listens on."""
    if uses_host_network(services):
        return []
    ports = published_ports(services)
    return [
        f"{stack.name}: route {route.subdomain} points at unpublished port {route.port}"
        for route in stack.routes
        if (str(route.port), "tcp") not in ports
    ]


def port_clashes(stacks: dict[str, Stack]) -> list[str]:
    """Host ports two stacks claim, across every stack, placed or not.

    Across all of them on purpose: any stack may be moved to any machine, and that only
    stays true if no two can ever collide. Routes count too, for the stacks on the host
    network, which publish nothing a Compose file can show.
    """
    owners: dict[tuple[str, str], set[str]] = {}
    for stack in stacks.values():
        services = _services(stack)
        for port in published_ports(services):
            owners.setdefault(port, set()).add(stack.name)
        for route in stack.routes:
            owners.setdefault((str(route.port), "tcp"), set()).add(stack.name)
    return sorted(
        f"{port}/{protocol}: {', '.join(sorted(names))}"
        for (port, protocol), names in owners.items()
        if len(names) > 1
    )


def _services(stack: Stack) -> dict[str, Any]:
    return yaml.safe_load(stack.compose_text).get("services") or {}


def service_name_clashes(stacks: dict[str, Stack]) -> list[str]:
    """Services two stacks both define: on one machine they would silently merge."""
    owners: dict[str, set[str]] = {}
    for stack in stacks.values():
        for service in _services(stack):
            owners.setdefault(service, set()).add(stack.name)
    return sorted(f"{s}: {', '.join(sorted(n))}" for s, n in owners.items() if len(n) > 1)


def check_stack(stack: Stack, env_text: str) -> list[str]:
    """Every rule one stack breaks; empty when clean."""
    services = _services(stack)
    rules = {
        "not in .env.example": undocumented_vars(stack.compose_text, env_text),
        "secret not required with :?": unguarded_secrets(stack.compose_text),
        "image not pinned": floating_images(services),
        "bad route": _route_port_problems(stack, services),
        "image outside its supported range": image_limit_problems(stack, services),
    }
    return [f"{stack.name}: {rule}: {item}" for rule, items in rules.items() for item in items]


def check_stacks(stacks: dict[str, Stack], env_text: str) -> list[str]:
    """Every rule broken across `stacks/` and `.env.example`; empty when clean."""
    findings = [f for stack in stacks.values() for f in check_stack(stack, env_text)]
    findings += [f"host port claimed twice: {clash}" for clash in port_clashes(stacks)]
    findings += [f"service defined twice: {clash}" for clash in service_name_clashes(stacks)]
    return findings
