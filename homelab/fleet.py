"""The fleet: what stacks exist, and which machine runs each.

`stacks/<name>/` holds a `compose.yaml` and a `stack.toml`; `inventory.toml` places stacks
on machines. Everything else (proxy routes, the dashboard, each machine's environment) is
derived from those, so moving a service is a one-word edit to the inventory.

Standard library only, like the rest of `python3 -m homelab`: a host has Docker, git and
`python3`, and no virtualenv.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

INVENTORY = "inventory.toml"
STACKS_DIR = "stacks"
PROXY_STACK = "proxy"

VARIABLE = re.compile(r"\$\{(?P<name>[A-Za-z_]\w*)(?P<operator>:?[-?+])?(?P<argument>[^}]*)\}")


class FleetError(ValueError):
    """The stack or inventory files cannot be read as written."""


@dataclass(frozen=True)
class Route:
    """A web interface, reached through the proxy at `<subdomain>.<domain>`."""

    subdomain: str
    name: str
    port: int
    group: str
    description: str = ""
    icon: str = ""
    path: str = ""
    # For a service that checks the Host header against its own idea of itself.
    host_header: str = ""
    # For a service that only speaks TLS, with a certificate of its own.
    upstream_tls: bool = False


@dataclass(frozen=True)
class ImageLimit:
    """Versions of an image this stack works with, as `major.minor`.

    A passing gate proves nothing about whether two services work together, because the
    tests start neither. This is that knowledge, written where the gate can enforce it.
    """

    image: str
    min: str = ""
    max: str = ""


@dataclass(frozen=True)
class Stack:
    name: str
    memory_mb: int
    compose_text: str = ""
    # May run on several machines at once, such as DNS and the backup client.
    multi: bool = False
    # Skipped with a note, rather than failing a deploy, until its variables are set.
    optional: bool = False
    # Stacks that must be placed somewhere for this one to work.
    requires: tuple[str, ...] = ()
    routes: tuple[Route, ...] = ()
    image_limits: tuple[ImageLimit, ...] = ()

    @property
    def required_vars(self) -> tuple[str, ...]:
        """Variables the Compose file refuses to run without (`${VAR:?}`)."""
        found = {
            match["name"]
            for match in VARIABLE.finditer(self.compose_text)
            if match["operator"] == ":?"
        }
        return tuple(sorted(found))

    def dirs_under(self, variable: str) -> tuple[str, ...]:
        """Mount paths under a root to create first, or Docker creates them owned by root."""
        mount = re.compile(r"\$\{" + variable + r"\}/(?P<path>[^:\s]+):")
        return tuple(sorted({match["path"] for match in mount.finditer(self.compose_text)}))


@dataclass(frozen=True)
class Machine:
    name: str
    address: str
    memory_mb: int
    stacks: tuple[str, ...]
    data_root: str = "data"
    media_root: str = "media"
    ssh: str = ""
    path: str = "homelab"
    tailscale_routes: str = ""


@dataclass(frozen=True)
class Site:
    """A route as actually placed: its hostname, and the machine that answers for it."""

    host: str
    route: Route
    machine: Machine
    # Its stack runs on several machines, so its name alone does not identify it.
    shared: bool = False


@dataclass(frozen=True)
class Fleet:
    domain: str
    machines: tuple[Machine, ...]
    stacks: dict[str, Stack]

    def machine(self, name: str) -> Machine:
        for machine in self.machines:
            if machine.name == name:
                return machine
        known = ", ".join(machine.name for machine in self.machines)
        raise FleetError(f"no machine named {name!r} in {INVENTORY}; it has: {known}")

    def running(self, stack: str) -> tuple[Machine, ...]:
        return tuple(machine for machine in self.machines if stack in machine.stacks)

    def address_of(self, stack: str) -> str:
        """The address of the first machine running `stack`; empty when none does."""
        machines = self.running(stack)
        return machines[0].address if machines else ""

    def unplaced(self) -> tuple[str, ...]:
        return tuple(sorted(name for name in self.stacks if not self.running(name)))

    def memory_used(self, machine: Machine) -> int:
        return sum(self.stacks[name].memory_mb for name in machine.stacks if name in self.stacks)

    def sites(self) -> tuple[Site, ...]:
        """One per route per machine; a stack on several machines gets `-<machine>` names."""
        sites: list[Site] = []
        for stack in self.stacks.values():
            machines = self.running(stack.name)
            shared = len(machines) > 1
            for machine in machines:
                suffix = f"-{machine.name}" if shared else ""
                sites.extend(
                    Site(f"{route.subdomain}{suffix}.{self.domain}", route, machine, shared)
                    for route in stack.routes
                )
        return tuple(sites)


def parse_route(raw: dict[str, Any], owner: str = "stack.toml") -> Route:
    try:
        return Route(**raw)
    except TypeError as exc:
        raise FleetError(f"{owner}: bad route: {exc}") from exc


def parse_image_limits(raw: Iterable[dict[str, Any]], name: str) -> tuple[ImageLimit, ...]:
    try:
        return tuple(ImageLimit(**limit) for limit in raw)
    except TypeError as exc:
        raise FleetError(f"stacks/{name}/stack.toml: bad image limit: {exc}") from exc


def parse_stack(name: str, toml_text: str, compose_text: str = "") -> Stack:
    try:
        raw = tomllib.loads(toml_text)
    except tomllib.TOMLDecodeError as exc:
        raise FleetError(f"stacks/{name}/stack.toml: {exc}") from exc
    if not isinstance(raw.get("memory_mb"), int):
        raise FleetError(f"stacks/{name}/stack.toml: memory_mb must be a whole number")
    return Stack(
        name=name,
        memory_mb=raw["memory_mb"],
        compose_text=compose_text,
        multi=raw.get("multi", False),
        optional=raw.get("optional", False),
        requires=tuple(raw.get("requires", ())),
        routes=tuple(
            parse_route(route, f"stacks/{name}/stack.toml") for route in raw.get("routes", ())
        ),
        image_limits=parse_image_limits(raw.get("image_limits", ()), name),
    )


def load_stacks(root: Path) -> dict[str, Stack]:
    """Every `stacks/<name>/`, by name. A folder missing either file is an error."""
    stacks = {}
    for folder in sorted((root / STACKS_DIR).iterdir()):
        if not folder.is_dir():
            continue
        missing = [n for n in ("compose.yaml", "stack.toml") if not (folder / n).is_file()]
        if missing:
            raise FleetError(f"stacks/{folder.name}/ has no {' or '.join(missing)}")
        stacks[folder.name] = parse_stack(
            folder.name,
            (folder / "stack.toml").read_text(encoding="utf-8"),
            (folder / "compose.yaml").read_text(encoding="utf-8"),
        )
    return stacks


def parse_inventory(toml_text: str, stacks: dict[str, Stack]) -> Fleet:
    try:
        raw = tomllib.loads(toml_text)
    except tomllib.TOMLDecodeError as exc:
        raise FleetError(f"{INVENTORY}: {exc}") from exc
    everywhere = tuple(raw.get("every_machine", ()))
    machines = []
    for name, entry in raw.get("machines", {}).items():
        placed = tuple(dict.fromkeys((*everywhere, *entry.pop("stacks", ()))))
        try:
            machines.append(Machine(name=name, stacks=placed, **entry))
        except TypeError as exc:
            raise FleetError(f"{INVENTORY}: machine {name}: {exc}") from exc
    if "domain" not in raw:
        raise FleetError(f"{INVENTORY}: no domain")
    return Fleet(domain=raw["domain"], machines=tuple(machines), stacks=stacks)


def load_fleet(root: Path) -> Fleet:
    text = (root / INVENTORY).read_text(encoding="utf-8")
    return parse_inventory(text, load_stacks(root))


def _placement_problems(fleet: Fleet) -> list[str]:
    problems: list[str] = []
    for machine in fleet.machines:
        problems.extend(
            f"{machine.name}: no stack named {name}"
            for name in machine.stacks
            if name not in fleet.stacks
        )
    for stack in fleet.stacks.values():
        machines = fleet.running(stack.name)
        if len(machines) > 1 and not stack.multi:
            names = ", ".join(machine.name for machine in machines)
            problems.append(f"{stack.name} can run on one machine, but is on: {names}")
        if machines:
            problems.extend(
                f"{stack.name} needs {needed}, which is on no machine"
                for needed in stack.requires
                if not fleet.running(needed)
            )
    return problems


def _capacity_problems(fleet: Fleet) -> list[str]:
    return [
        f"{machine.name} needs {fleet.memory_used(machine)} MB, over its "
        f"{machine.memory_mb} MB budget; move a stack to another machine"
        for machine in fleet.machines
        if fleet.memory_used(machine) > machine.memory_mb
    ]


def _duplicates(values: list[str]) -> list[str]:
    return sorted({value for value in values if values.count(value) > 1})


def validate_fleet(fleet: Fleet) -> list[str]:
    """Everything that would break this layout; empty when it is sound."""
    problems = [*_placement_problems(fleet), *_capacity_problems(fleet)]
    addresses = [machine.address for machine in fleet.machines]
    problems.extend(f"two machines share address {a}" for a in _duplicates(addresses))
    sites = fleet.sites()
    problems.extend(f"two routes share {h}" for h in _duplicates([s.host for s in sites]))
    if sites and not fleet.running(PROXY_STACK):
        problems.append(f"{len(sites)} routes need the {PROXY_STACK} stack on a machine")
    return problems
