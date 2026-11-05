"""Bringing a machine in line with the inventory."""

from __future__ import annotations

import os
import shlex
import socket
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from homelab import envfile, render
from homelab.fleet import PROXY_STACK, Fleet, FleetError, Machine, load_fleet, validate_fleet
from homelab.render import HostIds

PROJECT = "homelab"
ENV_FILE = ".env"
HOME_ASSISTANT_STACK = "home"
HOME_ASSISTANT_CONFIG = "home-assistant/configuration.yaml"

Runner = Callable[[Sequence[str]], int]


def run_in(root: Path) -> Runner:
    """A runner that streams to the terminal: on a host, the terminal is the interface."""

    def run(command: Sequence[str]) -> int:
        return subprocess.run(command, cwd=root, check=False).returncode

    return run


def render_group_id(lookup: Callable[[str], int]) -> int:
    """The group that may use `/dev/dri`, by name: `render`, or `video` on older systems."""
    for name in ("render", "video"):
        try:
            return lookup(name)
        except KeyError:
            continue
    return 0


def host_ids() -> HostIds:
    """This user's ids. Windows has none, and only `plan` and `rollout` run there."""
    if sys.platform == "win32":
        return HostIds()
    import grp

    return HostIds(
        os.getuid(), os.getgid(), render_group_id(lambda name: grp.getgrnam(name).gr_gid)
    )


def resolve_machine(fleet: Fleet, name: str | None) -> Machine:
    """The named machine, or the one whose name matches this computer's hostname."""
    return fleet.machine(name or socket.gethostname().split(".")[0].lower())


@dataclass(frozen=True)
class Selection:
    """Which of a machine's stacks a deploy will run, given what `.env` holds."""

    active: tuple[str, ...]
    skipped: dict[str, tuple[str, ...]]
    missing: dict[str, tuple[str, ...]]


def select_stacks(fleet: Fleet, machine: Machine, values: dict[str, str]) -> Selection:
    active, skipped, missing = [], {}, {}
    for name in machine.stacks:
        stack = fleet.stacks[name]
        unset = tuple(v for v in stack.required_vars if not values.get(v))
        if not unset:
            active.append(name)
        elif stack.optional:
            skipped[name] = unset
        else:
            missing[name] = unset
    return Selection(tuple(active), skipped, missing)


def compose_command(active: Sequence[str], args: Sequence[str]) -> list[str]:
    command = ["docker", "compose", "--project-name", PROJECT, "--project-directory", "."]
    command += ["--env-file", ENV_FILE, "--env-file", render.MACHINE_ENV]
    for name in active:
        command += ["--file", f"stacks/{name}/compose.yaml"]
    return [*command, *args]


def write_generated(root: Path, fleet: Fleet, env: dict[str, str]) -> None:
    (root / render.HOMEPAGE_DIR).mkdir(parents=True, exist_ok=True)
    files = {
        render.MACHINE_ENV: render.env_text(env),
        render.CADDYFILE: render.caddyfile(fleet),
        f"{render.HOMEPAGE_DIR}/services.yaml": render.homepage_services(fleet),
        f"{render.HOMEPAGE_DIR}/settings.yaml": render.homepage_settings(),
    }
    for path, text in files.items():
        (root / path).write_text(text, encoding="utf-8", newline="\n")


def prepare_data(fleet: Fleet, active: Sequence[str], env: dict[str, str]) -> None:
    """Create mount points as the deploying user, and seed config a service needs at birth."""
    data_root = Path(env["DATA_ROOT"])
    media_root = Path(env["MEDIA_ROOT"])
    data_root.mkdir(parents=True, exist_ok=True)
    media_root.mkdir(parents=True, exist_ok=True)
    for name in active:
        stack = fleet.stacks[name]
        for folder in stack.dirs_under("DATA_ROOT"):
            (data_root / folder).mkdir(parents=True, exist_ok=True)
        for folder in stack.dirs_under("MEDIA_ROOT"):
            (media_root / folder).mkdir(parents=True, exist_ok=True)
    seeded = data_root / HOME_ASSISTANT_CONFIG
    if HOME_ASSISTANT_STACK in active and not seeded.exists():
        seeded.write_text(render.home_assistant_config(fleet), encoding="utf-8", newline="\n")


def load_valid(root: Path) -> Fleet:
    fleet = load_fleet(root)
    problems = validate_fleet(fleet)
    if problems:
        raise FleetError("inventory.toml cannot work:\n  " + "\n  ".join(problems))
    return fleet


def prepare(root: Path, machine_name: str | None, ids: HostIds) -> tuple[Selection, Fleet]:
    """Everything before Docker runs: validate, select, generate. Raises if it cannot work."""
    fleet = load_valid(root)
    machine = resolve_machine(fleet, machine_name)
    env_path = root / ENV_FILE
    if not env_path.is_file():
        raise FleetError(f"no {ENV_FILE} here; run `python -m homelab init` and copy it over")
    env = render.machine_env(fleet, machine, root, ids)
    values = {**envfile.parse_env(env_path.read_text(encoding="utf-8")), **env}
    selection = select_stacks(fleet, machine, values)
    if selection.missing:
        lines = [f"{s}: {', '.join(v)}" for s, v in selection.missing.items()]
        raise FleetError(f"set these in {ENV_FILE} first:\n  " + "\n  ".join(lines))
    write_generated(root, fleet, env)
    prepare_data(fleet, selection.active, env)
    return selection, fleet


def deploy(root: Path, machine_name: str | None, run: Runner, ids: HostIds) -> int:
    """Pull and start this machine's stacks, and stop anything no longer placed here."""
    selection, _ = prepare(root, machine_name, ids)
    for name, unset in selection.skipped.items():
        print(f"skipping {name}: set {', '.join(unset)} to turn it on")
    steps = [["pull"], ["up", "--detach", "--remove-orphans"]]
    if PROXY_STACK in selection.active:
        steps.append(["exec", "-T", "caddy", "caddy", "reload", "--config", "/etc/caddy/Caddyfile"])
    for step in steps:
        code = run(compose_command(selection.active, step))
        if code != 0:
            return code
    return 0


def compose_passthrough(
    root: Path, machine_name: str | None, args: Sequence[str], run: Runner, ids: HostIds
) -> int:
    """`docker compose` scoped to what this machine runs, for `ps`, `logs` and the like."""
    selection, _ = prepare(root, machine_name, ids)
    return run(compose_command(selection.active, args))


def init_env(root: Path) -> str:
    """Create or top up `.env` from `.env.example`, generating blank secrets."""
    path = root / ENV_FILE
    example = (root / ".env.example").read_text(encoding="utf-8")
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    path.write_text(envfile.merge_example(example, existing), encoding="utf-8", newline="\n")
    values = envfile.parse_env(path.read_text(encoding="utf-8"))
    unset = sorted(name for name, value in values.items() if not value)
    return f"{ENV_FILE} ready. Still blank: {', '.join(unset) or 'nothing'}"


def rollout_commands(machine: Machine) -> tuple[list[str], list[str]]:
    """Copy the secrets over, then update the checkout and deploy, both over SSH."""
    target = machine.ssh or machine.address
    remote = shlex.join(["python3", "-m", "homelab", "deploy", "--machine", machine.name])
    script = f"cd {shlex.quote(machine.path)} && git pull --ff-only && {remote}"
    return ["scp", ENV_FILE, f"{target}:{machine.path}/{ENV_FILE}"], ["ssh", target, script]


def rollout(root: Path, names: Sequence[str], run: Runner) -> int:
    """Deploy to each named machine, or all of them; one failing does not stop the rest."""
    fleet = load_valid(root)
    machines = [fleet.machine(name) for name in names] if names else list(fleet.machines)
    failed = []
    for machine in machines:
        print(f"== {machine.name}")
        copy, update = rollout_commands(machine)
        if run(copy) != 0 or run(update) != 0:
            failed.append(machine.name)
    if failed:
        print(f"failed: {', '.join(failed)}")
    return 1 if failed else 0


def plan_text(fleet: Fleet) -> str:
    """Where everything runs, what each machine's budget looks like, and how to reach it."""
    lines = []
    for machine in fleet.machines:
        used = fleet.memory_used(machine)
        lines.append(f"{machine.name} ({machine.address}) {used}/{machine.memory_mb} MB")
        lines.append(f"  {', '.join(machine.stacks)}")
    if fleet.unplaced():
        lines.append(f"not placed: {', '.join(fleet.unplaced())}")
    lines.append("")
    lines.extend(f"https://{site.host}  -> {site.machine.name}" for site in fleet.sites())
    return "\n".join(lines)
