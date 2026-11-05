"""What a machine derives from the fleet: its environment and generated config.

These are how services on different machines find each other. Nothing here is edited by
hand; a change to `inventory.toml` reaches every machine on its next deploy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from homelab.fleet import PROXY_STACK, Fleet, Machine, Site

GENERATED_DIR = ".generated"
MACHINE_ENV = f"{GENERATED_DIR}/machine.env"
CADDYFILE = f"{GENERATED_DIR}/Caddyfile"
HOMEPAGE_DIR = f"{GENERATED_DIR}/homepage"
BACKUP_SERVER_STACK = "backup-server"
BACKUP_SERVER_PORT = 8000
MONITORING_STACK = "monitoring"
BESZEL_PORT = 8090

# Every variable `machine_env` sets. `tests/test_stack.py` treats these as documented,
# because a person never sets them in `.env`.
GENERATED_VARS = (
    "BACKUP_REPOSITORY",
    "BESZEL_HUB_URL",
    "DATA_ROOT",
    "HOMELAB_DOMAIN",
    "HOMELAB_MACHINE",
    "MEDIA_ROOT",
    "PGID",
    "PROXY_ADDRESS",
    "PUID",
    "RENDER_GID",
    "TAILSCALE_ROUTES",
)


@dataclass(frozen=True)
class HostIds:
    """The deploying user's ids, and the group that may use the GPU."""

    uid: int = 1000
    gid: int = 1000
    render_gid: int = 0


def absolute(path: str, root: Path) -> str:
    """A root from the inventory as an absolute path, so a mount never depends on the cwd."""
    return (root / path).resolve().as_posix()


def machine_env(fleet: Fleet, machine: Machine, root: Path, ids: HostIds) -> dict[str, str]:
    backup = fleet.address_of(BACKUP_SERVER_STACK)
    hub = fleet.address_of(MONITORING_STACK)
    return {
        "BACKUP_REPOSITORY": (
            f"rest:http://{backup}:{BACKUP_SERVER_PORT}/{machine.name}" if backup else ""
        ),
        "BESZEL_HUB_URL": f"http://{hub}:{BESZEL_PORT}" if hub else "",
        "DATA_ROOT": absolute(machine.data_root, root),
        "HOMELAB_DOMAIN": fleet.domain,
        "HOMELAB_MACHINE": machine.name,
        "MEDIA_ROOT": absolute(machine.media_root, root),
        "PGID": str(ids.gid),
        "PROXY_ADDRESS": fleet.address_of(PROXY_STACK),
        "PUID": str(ids.uid),
        "RENDER_GID": str(ids.render_gid),
        "TAILSCALE_ROUTES": machine.tailscale_routes,
    }


def env_text(values: dict[str, str]) -> str:
    return "".join(f"{name}={value}\n" for name, value in values.items())


def _caddy_site(site: Site) -> str:
    upstream = f"{site.machine.address}:{site.route.port}"
    if site.route.upstream_tls:
        upstream = f"https://{upstream}"
    options = []
    if site.route.host_header:
        options.append(f"header_up Host {site.route.host_header}")
    if site.route.upstream_tls:
        options.append("transport http {\n\t\t\ttls_insecure_skip_verify\n\t\t}")
    if not options:
        return f"{site.host} {{\n\treverse_proxy {upstream}\n}}\n"
    body = "".join(f"\t\t{option}\n" for option in options)
    return f"{site.host} {{\n\treverse_proxy {upstream} {{\n{body}\t}}\n}}\n"


def caddyfile(fleet: Fleet) -> str:
    """Every site, served with a certificate from Caddy's own local authority."""
    header = "{\n\tlocal_certs\n\tskip_install_trust\n}\n"
    return "\n".join([header, *(_caddy_site(site) for site in fleet.sites())])


def homepage_services(fleet: Fleet) -> str:
    """Homepage's `services.yaml`. JSON strings are valid YAML, so nothing needs a parser."""
    groups: dict[str, list[Site]] = {}
    for site in fleet.sites():
        groups.setdefault(site.route.group, []).append(site)
    lines = []
    for group in sorted(groups):
        lines.append(f"- {json.dumps(group)}:")
        for site in sorted(groups[group], key=lambda s: s.host):
            route = site.route
            lines.append(f"    - {json.dumps(_title(site))}:")
            lines.append(f"        href: {json.dumps(f'https://{site.host}{route.path}')}")
            lines.append(f"        description: {json.dumps(_tile_note(site))}")
            monitor = f"http://{site.machine.address}:{route.port}"
            if not route.upstream_tls:
                lines.append(f"        siteMonitor: {json.dumps(monitor)}")
            if route.icon:
                lines.append(f"        icon: {json.dumps(route.icon)}")
    return "\n".join(lines) + "\n"


def _title(site: Site) -> str:
    return f"{site.route.name} ({site.machine.name})" if site.shared else site.route.name


def _tile_note(site: Site) -> str:
    note = site.route.description
    return f"{note} on {site.machine.name}" if note else f"On {site.machine.name}"


def homepage_settings() -> str:
    return 'title: "Homelab"\nheaderStyle: clean\nhideVersion: true\n'


def home_assistant_config(fleet: Fleet) -> str:
    """Seeded once: without trusting the proxy, Home Assistant answers it with a 400."""
    return (
        "default_config:\n\n"
        "http:\n"
        "  use_x_forwarded_for: true\n"
        "  trusted_proxies:\n"
        f"    - {fleet.address_of(PROXY_STACK)}\n"
    )
