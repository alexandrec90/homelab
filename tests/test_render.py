"""What a machine derives from the inventory."""

from pathlib import Path

import yaml

from homelab import render
from homelab.fleet import Fleet, Machine, Route, Stack
from homelab.render import HostIds

ROOT = Path(__file__).resolve().parent


def placed(routes=(), *, stacks=("web", "proxy")):
    available = {
        "web": Stack("web", 1, routes=routes),
        "proxy": Stack("proxy", 1),
        "backup-server": Stack("backup-server", 1),
        "monitoring": Stack("monitoring", 1),
    }
    box = Machine("box", "10.0.0.5", 1000, tuple(stacks))
    return Fleet("lan", (box,), available)


def test_generated_vars_lists_exactly_what_machine_env_sets():
    fleet = placed()
    env = render.machine_env(fleet, fleet.machines[0], ROOT, HostIds())
    assert tuple(sorted(env)) == render.GENERATED_VARS


def test_machine_env_derives_addresses_from_placement():
    fleet = placed(stacks=("proxy", "backup-server", "monitoring"))
    env = render.machine_env(fleet, fleet.machines[0], ROOT, HostIds(1001, 1002, 44))
    assert env["PROXY_ADDRESS"] == "10.0.0.5"
    assert env["BACKUP_REPOSITORY"] == "rest:http://10.0.0.5:8000/box"
    assert env["BESZEL_HUB_URL"] == "http://10.0.0.5:8090"
    assert (env["PUID"], env["PGID"], env["RENDER_GID"]) == ("1001", "1002", "44")


def test_machine_env_is_blank_for_what_is_placed_nowhere():
    fleet = placed(stacks=())
    env = render.machine_env(fleet, fleet.machines[0], ROOT, HostIds())
    assert env["BACKUP_REPOSITORY"] == ""
    assert env["BESZEL_HUB_URL"] == ""


def test_absolute_keeps_an_absolute_root_and_anchors_a_relative_one():
    assert render.absolute("data", ROOT) == (ROOT / "data").as_posix()
    assert render.absolute(ROOT.as_posix(), Path("elsewhere")) == ROOT.as_posix()


def test_env_text_is_one_assignment_per_line():
    assert render.env_text({"A": "1", "B": ""}) == "A=1\nB=\n"


def test_caddyfile_routes_a_name_to_the_machine_that_runs_it():
    text = render.caddyfile(placed((Route("web", "Web", 8080, "G"),)))
    assert "local_certs" in text
    assert "web.lan {\n\treverse_proxy 10.0.0.5:8080\n}\n" in text


def test_caddyfile_rewrites_the_host_header_when_a_route_asks():
    route = Route("sync", "Sync", 8384, "G", host_header="localhost")
    text = render.caddyfile(placed((route,)))
    assert "reverse_proxy 10.0.0.5:8384 {\n\t\theader_up Host localhost\n\t}" in text


def test_caddyfile_accepts_an_upstream_certificate_when_a_route_asks():
    route = Route("cam", "Cam", 8971, "G", upstream_tls=True)
    text = render.caddyfile(placed((route,)))
    assert "reverse_proxy https://10.0.0.5:8971 {" in text
    assert "tls_insecure_skip_verify" in text


def test_a_plain_route_does_not_skip_certificate_checks():
    text = render.caddyfile(placed((Route("web", "Web", 8080, "G"),)))
    assert "tls_insecure_skip_verify" not in text


def test_homepage_services_is_yaml_homepage_can_read():
    routes = (
        Route("web", "Web", 8080, "Apps", 'Say "hi": it\'s', "web.png", "/admin"),
        Route("b", "B", 1234, "Apps"),
        Route("cam", "Cam", 8971, "Home", upstream_tls=True),
    )
    parsed = yaml.safe_load(render.homepage_services(placed(routes)))
    apps, home = parsed
    tiles = {name: tile for entry in apps["Apps"] for name, tile in entry.items()}
    assert tiles["Web"]["href"] == "https://web.lan/admin"
    assert tiles["Web"]["description"] == 'Say "hi": it\'s on box'
    assert tiles["Web"]["siteMonitor"] == "http://10.0.0.5:8080"
    assert tiles["Web"]["icon"] == "web.png"
    assert tiles["B"]["description"] == "On box"
    assert "siteMonitor" not in home["Home"][0]["Cam"]


def test_a_tile_on_a_shared_stack_names_its_machine():
    fleet = placed((Route("web", "Web", 8080, "Apps"),))
    second = Machine("two", "10.0.0.6", 1000, ("web",))
    fleet = Fleet("lan", (*fleet.machines, second), fleet.stacks)
    text = render.homepage_services(fleet)
    assert "Web (box)" in text
    assert "Web (two)" in text


def test_homepage_settings_is_yaml():
    assert yaml.safe_load(render.homepage_settings())["title"] == "Homelab"


def test_home_assistant_config_trusts_the_proxy():
    config = yaml.safe_load(render.home_assistant_config(placed()))
    assert config["http"]["trusted_proxies"] == ["10.0.0.5"]
    assert config["http"]["use_x_forwarded_for"] is True
