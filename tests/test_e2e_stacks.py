"""The stacks against real Docker: the tier `scripts/e2e.py` runs and no other run does.

Everything else in `tests/` reads text, so nothing there can say Docker accepts what the
deploy tool produces, that a pin still names an image, or that the proxy loads the config
generated for it. These drive `python -m homelab` the way a host does.

Every test works in a copy of the checkout under `tmp_path`, so a real `.env`,
`.generated/` and `data/` are never touched. No stack runs as written, because this
workstation is not a host and its ports are not free: the one container started is the
proxy, with its published ports taken out, and it publishes nothing while it runs.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from homelab import envfile, stack
from homelab.deploy import PROJECT
from homelab.fleet import PROXY_STACK, load_fleet, load_stacks

pytestmark = [pytest.mark.local, pytest.mark.usefixtures("docker")]

REPO = Path(__file__).resolve().parent.parent
PLACEHOLDER = "e2e-placeholder"
# Long enough for a first pull of the proxy image; a hung daemon must still end the night.
TIMEOUT_SECONDS = 600
EDGE = "edge"
PUBLISHED_PORTS = re.compile(r"^ +ports:\n(?: +- .*\n)+", re.MULTILINE)
# By address: from inside the image, `wget` to `localhost:2019` is refused.
CADDY_SERVERS = "http://127.0.0.1:2019/config/apps/http/servers"

MACHINES = [machine.name for machine in load_fleet(REPO).machines]
IMAGES = sorted(
    {
        str(service["image"])
        for one in load_stacks(REPO).values()
        for service in stack._services(one).values()
    }
)


def run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=TIMEOUT_SECONDS,
        check=False,
    )


def homelab(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """`python -m homelab` as a host runs it: `-S` leaves out every installed package."""
    return run([sys.executable, "-S", "-m", "homelab", *args], root)


def passed(done: subprocess.CompletedProcess[str]) -> subprocess.CompletedProcess[str]:
    assert done.returncode == 0, f"{done.args}\n{done.stdout}\n{done.stderr}"
    return done


def place(root: Path, machines: dict[str, list[str]]) -> None:
    """Replace the inventory, with room on each machine for whatever it is given."""
    lines = ['domain = "home.arpa"']
    for number, (name, stacks) in enumerate(machines.items(), start=1):
        lines.append(f'[machines.{name}]\naddress = "10.0.0.{number}"')
        lines.append(f"memory_mb = 1000000\nstacks = {json.dumps(stacks)}")
    (root / "inventory.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture(scope="module")
def docker() -> None:
    """A failure, not a skip: with no Docker the whole tier would skip, and that is red too."""
    if shutil.which("docker") is None:
        pytest.fail("no docker on PATH")
    if run(["docker", "info"]).returncode != 0:
        pytest.fail("the Docker daemon is not running")


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """What a host clones, set up by `init`, with every stack's variables filled in."""
    for name in ("homelab", "stacks"):
        ignore = shutil.ignore_patterns("__pycache__")
        shutil.copytree(REPO / name, tmp_path / name, ignore=ignore)
    for name in ("inventory.toml", ".env.example"):
        shutil.copy(REPO / name, tmp_path / name)
    passed(homelab(tmp_path, "init"))
    env = tmp_path / ".env"
    values = envfile.parse_env(env.read_text(encoding="utf-8"))
    text = "".join(f"{name}={value or PLACEHOLDER}\n" for name, value in values.items())
    env.write_text(text, encoding="utf-8", newline="\n")
    return tmp_path


@pytest.fixture
def proxy_deploy(checkout: Path) -> Iterator[subprocess.CompletedProcess[str]]:
    """`deploy` of the real proxy, unpublished, with the rest of the fleet behind it.

    A deploy always uses the project name `homelab` and removes that project's orphans, so
    on a machine already running one this would take the real services down. It refuses.
    """
    label = f"label=com.docker.compose.project={PROJECT}"
    existing = passed(run(["docker", "ps", "--all", "--quiet", "--filter", label])).stdout
    if existing.strip():
        pytest.fail(f"this machine already has a compose project named {PROJECT}")
    place(checkout, {EDGE: [PROXY_STACK], "rest": sorted(set(load_stacks(checkout)) - {"proxy"})})
    compose = checkout / "stacks" / PROXY_STACK / "compose.yaml"
    text, removed = PUBLISHED_PORTS.subn("", compose.read_text(encoding="utf-8"))
    assert removed == 1, "the proxy stack publishes its ports some other way now"
    compose.write_text(text, encoding="utf-8", newline="\n")
    try:
        yield homelab(checkout, "deploy", "--machine", EDGE)
    finally:
        homelab(checkout, "compose", "--machine", EDGE, "down", "--volumes")


@pytest.mark.parametrize("machine", MACHINES)
def test_compose_accepts_what_a_machine_deploys(checkout, machine):
    passed(homelab(checkout, "compose", "--machine", machine, "config", "--quiet"))


def test_compose_accepts_every_stack_on_one_machine(checkout):
    """Unplaced stacks included: the inventory may move any stack next to any other."""
    place(checkout, {"everything": sorted(load_stacks(checkout))})
    passed(homelab(checkout, "compose", "--machine", "everything", "config", "--quiet"))


@pytest.mark.parametrize("image", IMAGES)
def test_a_pinned_image_is_on_its_registry(image):
    """Regression guard for the pin a host's `docker compose pull` would stop on."""
    passed(run(["docker", "manifest", "inspect", image]))


def test_deploy_serves_the_whole_fleet_from_a_real_proxy(checkout, proxy_deploy):
    """Pull, up and Caddy's reload, then every route read back out of the running proxy."""
    passed(proxy_deploy)
    command = ("exec", "-T", "caddy", "wget", "-qO-", CADDY_SERVERS)
    served = passed(homelab(checkout, "compose", "--machine", EDGE, *command)).stdout
    sites = load_fleet(checkout).sites()
    assert sites, "the fleet has no routes, so this proves nothing"
    assert [site.host for site in sites if site.host not in served] == []
