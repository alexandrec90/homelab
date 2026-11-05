"""Reading and checking the fleet, and the repository's own inventory."""

import ast
import sys
from pathlib import Path

import pytest

from homelab import fleet as fl
from homelab.fleet import Fleet, FleetError, ImageLimit, Machine, Route, Site, Stack

REPO = Path(__file__).resolve().parent.parent
ROUTE = Route("web", "Web", 8080, "Apps")


def machine(name="a", stacks=(), memory_mb=1000, address=""):
    return Machine(name, address or f"10.0.0.{ord(name[0])}", memory_mb, tuple(stacks))


def stack(name, memory_mb=100, **kwargs):
    return Stack(name=name, memory_mb=memory_mb, **kwargs)


def fleet(*machines, stacks=()):
    return Fleet("lan", tuple(machines), {s.name: s for s in stacks})


def write_stack(root, name, toml="memory_mb = 1\n", compose="services: {}\n"):
    folder = root / "stacks" / name
    folder.mkdir(parents=True)
    (folder / "stack.toml").write_text(toml, encoding="utf-8")
    (folder / "compose.yaml").write_text(compose, encoding="utf-8")


def test_the_repository_inventory_is_sound():
    assert fl.validate_fleet(fl.load_fleet(REPO)) == []


def test_every_stack_in_the_repository_is_placed_but_cameras():
    assert fl.load_fleet(REPO).unplaced() == ("cameras",)


def test_the_host_tool_imports_only_the_standard_library():
    """A host has no virtualenv, so a third-party import is a crash on every machine."""
    allowed = {"homelab", "__future__"}
    for name in ("cli", "deploy", "envfile", "fleet", "render", "__main__", "__init__"):
        tree = ast.parse((REPO / "homelab" / f"{name}.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                top = module.split(".")[0]
                assert top in allowed or top in sys.stdlib_module_names, name
                assert module != "homelab.stack", name


def test_required_vars_are_only_the_ones_with_no_fallback():
    text = "${A:?x} ${B:-y} ${C} ${A:?again}"
    assert stack("s", compose_text=text).required_vars == ("A",)


def test_dirs_under_lists_mounts_below_the_root_only():
    text = "- ${DATA_ROOT}/b/c:/x\n- ${DATA_ROOT}/a:/y:ro\n- ${DATA_ROOT}:/z\n- ${OTHER}/d:/w\n"
    assert stack("s", compose_text=text).dirs_under("DATA_ROOT") == ("a", "b/c")


def test_machine_lookup_names_the_options_on_a_miss():
    with pytest.raises(FleetError, match="it has: a, b"):
        fleet(machine("a"), machine("b")).machine("c")


def test_machine_lookup_finds_by_name():
    a = machine("a")
    assert fleet(a).machine("a") is a


def test_running_and_address_of():
    a, b = machine("a", ["x"]), machine("b", ["x", "y"])
    placed = fleet(a, b, stacks=[stack("x"), stack("y")])
    assert placed.running("x") == (a, b)
    assert placed.address_of("y") == b.address


def test_address_of_a_stack_on_no_machine_is_empty():
    assert fleet(machine("a")).address_of("nothing") == ""


def test_unplaced_lists_stacks_on_no_machine():
    assert fleet(machine("a", ["x"]), stacks=[stack("x"), stack("y")]).unplaced() == ("y",)


def test_memory_used_adds_up_placed_stacks():
    placed = fleet(machine("a", ["x", "y"]), stacks=[stack("x", 300), stack("y", 400)])
    assert placed.memory_used(placed.machines[0]) == 700


def test_a_site_on_one_machine_is_named_for_its_route():
    a = machine("a", ["x"])
    placed = fleet(a, stacks=[stack("x", routes=(ROUTE,))])
    assert placed.sites() == (Site("web.lan", ROUTE, a, shared=False),)


def test_sites_on_two_machines_are_named_for_each():
    placed = fleet(
        machine("a", ["x"]), machine("b", ["x"]), stacks=[stack("x", multi=True, routes=(ROUTE,))]
    )
    assert [s.host for s in placed.sites()] == ["web-a.lan", "web-b.lan"]
    assert all(s.shared for s in placed.sites())


def test_parse_stack_reads_every_field():
    text = """
memory_mb = 64
multi = true
optional = true
requires = ["proxy"]

[[routes]]
subdomain = "web"
name = "Web"
port = 8080
group = "Apps"
"""
    parsed = fl.parse_stack("s", text, "compose")
    assert parsed == Stack("s", 64, "compose", True, True, ("proxy",), (ROUTE,))


@pytest.mark.parametrize("text", ["", "memory_mb = 'lots'\n", "not toml"])
def test_parse_stack_refuses_a_bad_descriptor(text):
    with pytest.raises(FleetError, match=r"stacks/s/stack\.toml"):
        fl.parse_stack("s", text)


def test_parse_stack_refuses_an_unknown_route_key():
    text = 'memory_mb = 1\n[[routes]]\nsubdomain = "a"\nname = "A"\nport = 1\ngroup = "G"\nz = 1\n'
    with pytest.raises(FleetError, match="bad route"):
        fl.parse_stack("s", text)


def test_parse_stack_reads_image_limits():
    text = 'memory_mb = 1\n[[image_limits]]\nimage = "mariadb"\nmin = "10.11"\nmax = "12.3"\n'
    assert fl.parse_stack("s", text).image_limits == (ImageLimit("mariadb", "10.11", "12.3"),)


def test_parse_image_limits_refuses_an_unknown_key():
    with pytest.raises(FleetError, match="bad image limit"):
        fl.parse_image_limits([{"image": "mariadb", "maximum": "12.3"}], "s")


def test_the_repository_limits_mariadb_to_what_nextcloud_supports():
    limits = fl.load_stacks(REPO)["cloud"].image_limits
    assert limits == (ImageLimit("mariadb", "10.11", "12.3"),)


def test_parse_route_reads_optional_fields():
    raw = {"subdomain": "a", "name": "A", "port": 1, "group": "G", "upstream_tls": True}
    assert fl.parse_route(raw).upstream_tls


def test_load_stacks_reads_each_folder(tmp_path):
    write_stack(tmp_path, "b")
    write_stack(tmp_path, "a", compose="services: {a: {}}\n")
    (tmp_path / "stacks" / "README.md").write_text("not a stack", encoding="utf-8")
    stacks = fl.load_stacks(tmp_path)
    assert list(stacks) == ["a", "b"]
    assert "services" in stacks["a"].compose_text


def test_load_stacks_refuses_a_half_written_stack(tmp_path):
    (tmp_path / "stacks" / "x").mkdir(parents=True)
    with pytest.raises(FleetError, match=r"stacks/x/ has no compose.yaml or stack.toml"):
        fl.load_stacks(tmp_path)


def test_parse_inventory_adds_every_machine_stacks_first_without_repeats():
    text = """
domain = "lan"
every_machine = ["agent", "backup"]

[machines.a]
address = "10.0.0.1"
memory_mb = 1000
stacks = ["web", "backup"]
data_root = "/mnt/data"
"""
    parsed = fl.parse_inventory(text, {})
    assert parsed.domain == "lan"
    assert parsed.machines[0].stacks == ("agent", "backup", "web")
    assert parsed.machines[0].data_root == "/mnt/data"


def test_parse_inventory_refuses_an_unknown_machine_key():
    text = 'domain = "lan"\n[machines.a]\naddress = "1"\nmemory_mb = 1\nbudget = 2\n'
    with pytest.raises(FleetError, match="machine a"):
        fl.parse_inventory(text, {})


def test_parse_inventory_refuses_a_missing_domain():
    with pytest.raises(FleetError, match="no domain"):
        fl.parse_inventory("", {})


def test_parse_inventory_refuses_bad_toml():
    with pytest.raises(FleetError, match=r"inventory\.toml"):
        fl.parse_inventory("not toml", {})


def test_load_fleet_joins_the_inventory_to_the_stacks(tmp_path):
    write_stack(tmp_path, "web")
    (tmp_path / "inventory.toml").write_text(
        'domain = "lan"\n[machines.a]\naddress = "1"\nmemory_mb = 9\nstacks = ["web"]\n',
        encoding="utf-8",
    )
    loaded = fl.load_fleet(tmp_path)
    assert loaded.machines[0].stacks == ("web",)
    assert "web" in loaded.stacks


def test_a_sound_fleet_has_no_problems():
    assert fl.validate_fleet(fleet(machine("a", ["x"]), stacks=[stack("x")])) == []


def test_an_unknown_stack_is_a_problem():
    assert fl.validate_fleet(fleet(machine("a", ["x"]))) == ["a: no stack named x"]


def test_a_single_machine_stack_on_two_machines_is_a_problem():
    placed = fleet(machine("a", ["x"]), machine("b", ["x"]), stacks=[stack("x")])
    assert fl.validate_fleet(placed) == ["x can run on one machine, but is on: a, b"]


def test_a_multi_stack_may_be_on_two_machines():
    placed = fleet(machine("a", ["x"]), machine("b", ["x"]), stacks=[stack("x", multi=True)])
    assert fl.validate_fleet(placed) == []


def test_a_missing_requirement_is_a_problem():
    placed = fleet(machine("a", ["x"]), stacks=[stack("x", requires=("y",)), stack("y")])
    assert fl.validate_fleet(placed) == ["x needs y, which is on no machine"]


def test_an_unplaced_stacks_requirements_do_not_matter():
    assert fl.validate_fleet(fleet(stacks=[stack("x", requires=("y",))])) == []


def test_a_machine_over_its_budget_is_a_problem():
    placed = fleet(machine("a", ["x"], memory_mb=99), stacks=[stack("x", 100)])
    assert "a needs 100 MB, over its 99 MB budget" in fl.validate_fleet(placed)[0]


def test_a_machine_exactly_at_budget_is_fine():
    placed = fleet(machine("a", ["x"], memory_mb=100), stacks=[stack("x", 100)])
    assert fl.validate_fleet(placed) == []


def test_two_machines_on_one_address_is_a_problem():
    placed = fleet(machine("a", address="10.0.0.1"), machine("b", address="10.0.0.1"))
    assert fl.validate_fleet(placed) == ["two machines share address 10.0.0.1"]


def test_two_routes_on_one_name_is_a_problem():
    placed = fleet(
        machine("a", ["x", "y", "proxy"]),
        stacks=[stack("x", routes=(ROUTE,)), stack("y", routes=(ROUTE,)), stack("proxy")],
    )
    assert fl.validate_fleet(placed) == ["two routes share web.lan"]


def test_routes_with_no_proxy_placed_is_a_problem():
    placed = fleet(machine("a", ["x"]), stacks=[stack("x", routes=(ROUTE,))])
    assert fl.validate_fleet(placed) == ["1 routes need the proxy stack on a machine"]
