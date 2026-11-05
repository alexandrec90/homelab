"""The rules in `homelab/stack.py`, and the repository's own stacks held to them."""

import re
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from homelab import stack
from homelab.fleet import ImageLimit, Route, Stack, load_stacks

REPO = Path(__file__).resolve().parent.parent

CLEAN_COMPOSE = """
services:
  web:
    image: nginx:1.27.0
    ports:
      - "8080:80"
    environment:
      API_PASSWORD: ${API_PASSWORD:?required}
"""
CLEAN_ENV = "API_PASSWORD=\n"


def web(name="web", port="8080", service="web", routes=()):
    compose = CLEAN_COMPOSE.replace("8080", port).replace("  web:", f"  {service}:")
    return Stack(name=name, memory_mb=1, compose_text=compose, routes=routes)


def test_the_repository_stacks_break_no_rule():
    stacks = load_stacks(REPO)
    env = (REPO / ".env.example").read_text(encoding="utf-8")
    assert stack.check_stacks(stacks, env) == []


def test_a_clean_stack_has_no_findings():
    assert stack.check_stack(web(), CLEAN_ENV) == []


def test_a_compose_file_with_no_services_has_no_findings():
    empty = Stack(name="empty", memory_mb=1, compose_text="name: empty\n")
    assert stack.check_stack(empty, "") == []


def test_referenced_vars_records_each_operator_a_name_is_used_with():
    text = "${A} ${A:-x} ${B:?why} ${C-y}"
    assert stack.referenced_vars(text) == {"A": {"", ":-"}, "B": {":?"}, "C": {"-"}}


def test_documented_vars_ignores_a_commented_out_key():
    assert stack.documented_vars("# OFF=1\nON=1\n") == {"ON"}


def test_undocumented_vars_lists_only_what_the_example_lacks():
    compose = "${KNOWN} ${MISSING_B} ${MISSING_A:-x}"
    assert stack.undocumented_vars(compose, "KNOWN=1\n") == ["MISSING_A", "MISSING_B"]


def test_a_generated_variable_counts_as_documented():
    assert stack.undocumented_vars("${DATA_ROOT}", "") == []


def test_a_variable_missing_from_the_example_is_reported():
    assert stack.check_stack(web(), "") == ["web: not in .env.example: API_PASSWORD"]


@pytest.mark.parametrize("use", ["${DB_PASSWORD}", "${DB_PASSWORD:-hunter2}", "${API_SECRET:-}"])
def test_a_secret_that_can_be_blank_is_reported(use):
    name = stack.referenced_vars(use).popitem()[0]
    assert stack.unguarded_secrets(use) == [name]


def test_a_secret_is_reported_when_any_one_use_is_unguarded():
    assert stack.unguarded_secrets("${DB_PASSWORD:?x} ${DB_PASSWORD}") == ["DB_PASSWORD"]


def test_a_required_secret_passes():
    assert stack.unguarded_secrets("${DB_PASSWORD:?required}") == []


def test_a_non_secret_may_have_a_default():
    assert stack.unguarded_secrets("${TZ:-Etc/UTC}") == []


@pytest.mark.parametrize(
    "image",
    ["nginx", "nginx:latest", "ghcr.io/org/app:stable", "registry:5000/app", "app:release"],
)
def test_a_floating_image_is_reported(image):
    assert stack.floating_images({"web": {"image": image}}) == [f"web: {image}"]


@pytest.mark.parametrize(
    "image",
    ["nginx:1.27.0", "registry:5000/app:1.2", "nginx@sha256:abc", "plexinc/pms-docker:1.43.6-d1e"],
)
def test_a_pinned_image_passes(image):
    assert stack.floating_images({"web": {"image": image}}) == []


def test_a_service_with_no_image_is_reported():
    assert stack.floating_images({"web": {"build": "."}}) == ["web: None"]


def test_published_ports_reads_past_a_bind_address_and_a_default():
    services = {"a": {"ports": ["127.0.0.1:8080:80", "${P:-53}:53/udp", "9000"]}}
    expected = {("8080", "tcp"), ("53", "udp"), ("9000", "tcp")}
    assert stack.published_ports(services) == expected


def test_a_service_with_no_ports_publishes_nothing():
    assert stack.published_ports({"a": {}}) == set()


def test_uses_host_network():
    assert stack.uses_host_network({"a": {"network_mode": "host"}, "b": {}})
    assert not stack.uses_host_network({"a": {"network_mode": "bridge"}, "b": {}})


def test_two_stacks_on_one_host_port_are_reported_even_unplaced():
    stacks = {"a": web("a", service="one"), "b": web("b", service="two")}
    assert stack.port_clashes(stacks) == ["8080/tcp: a, b"]


def test_a_stack_may_use_a_port_for_both_protocols():
    dns = Stack(
        name="dns",
        memory_mb=1,
        compose_text='services:\n  d:\n    ports: ["53:53/tcp", "53:53/udp"]\n',
    )
    assert stack.port_clashes({"dns": dns}) == []


def test_tcp_and_udp_on_one_port_do_not_clash_across_stacks():
    udp = Stack(name="a", memory_mb=1, compose_text='services:\n  a:\n    ports: ["53:53/udp"]\n')
    tcp = Stack(name="b", memory_mb=1, compose_text='services:\n  b:\n    ports: ["53:53/tcp"]\n')
    assert stack.port_clashes({"a": udp, "b": tcp}) == []


def test_a_host_network_route_clashes_with_a_published_port():
    host = Stack(
        name="host",
        memory_mb=1,
        compose_text="services:\n  h:\n    network_mode: host\n",
        routes=(Route("h", "H", 8080, "G"),),
    )
    assert stack.port_clashes({"host": host, "web": web()}) == ["8080/tcp: host, web"]


def test_a_route_to_an_unpublished_port_is_reported():
    routes = (Route("web", "Web", 9999, "G"),)
    expected = ["web: bad route: web: route web points at unpublished port 9999"]
    assert stack.check_stack(web(routes=routes), CLEAN_ENV) == expected


def test_a_route_on_the_host_network_needs_no_published_port():
    host = Stack(
        name="host",
        memory_mb=1,
        compose_text="services:\n  h:\n    image: a:1\n    network_mode: host\n",
        routes=(Route("h", "H", 8123, "G"),),
    )
    assert stack.check_stack(host, "") == []


def test_one_service_name_in_two_stacks_is_reported():
    stacks = {"a": web("a"), "b": web("b", port="8081")}
    assert stack.service_name_clashes(stacks) == ["web: a, b"]


def cloud_with(image):
    """The repository's own `cloud` stack, with its database image swapped.

    Matches any tag: Dependabot moves this pin, and the test must not depend on where to.
    """
    cloud = load_stacks(REPO)["cloud"]
    text, swapped = re.subn(r"\bmariadb:\S+", image, cloud.compose_text)
    assert swapped, "the cloud stack pins no mariadb image"
    return replace(cloud, compose_text=text)


def test_the_repository_refuses_a_database_nextcloud_does_not_support():
    """Regression: Dependabot's PR #5 proposed this, and the gate passed it."""
    expected = "image outside its supported range: mariadb:13.0.2 is past 12.3"
    assert any(expected in f for f in stack.check_stack(cloud_with("mariadb:13.0.2"), ""))


def limited(image, **limit):
    compose = f"services:\n  db:\n    image: {image}\n"
    return Stack("db", 1, compose, image_limits=(ImageLimit("mariadb", **limit),))


def test_an_image_inside_its_limits_passes():
    stack_ = limited("mariadb:11.4.13", min="10.11", max="12.3")
    assert stack.image_limit_problems(stack_, stack._services(stack_)) == []


def test_a_patch_release_of_the_newest_supported_version_passes():
    stack_ = limited("mariadb:12.3.9", max="12.3")
    assert stack.image_limit_problems(stack_, stack._services(stack_)) == []


def test_an_image_below_its_minimum_is_reported():
    stack_ = limited("mariadb:10.6.2", min="10.11")
    problems = stack.image_limit_problems(stack_, stack._services(stack_))
    assert problems == ["mariadb:10.6.2 is before 10.11"]


def test_versions_compare_as_numbers_not_text():
    stack_ = limited("mariadb:9.9.9", max="10.11")
    assert stack.image_limit_problems(stack_, stack._services(stack_)) == []


def test_a_limit_covers_a_registry_prefix():
    stack_ = limited("docker.io/library/mariadb:13.0.2", max="12.3")
    assert len(stack.image_limit_problems(stack_, stack._services(stack_))) == 1


def test_a_limit_leaves_other_images_alone():
    compose = "services:\n  a:\n    image: mariadb:11.4.13\n  b:\n    image: mariadb-backup:13.0\n"
    stack_ = Stack("db", 1, compose, image_limits=(ImageLimit("mariadb", max="12.3"),))
    assert stack.image_limit_problems(stack_, stack._services(stack_)) == []


def test_a_tag_that_cannot_be_compared_is_reported_not_passed():
    """A limit that silently stops applying is worse than none."""
    stack_ = limited("mariadb:lts", max="12.3")
    problems = stack.image_limit_problems(stack_, stack._services(stack_))
    assert problems == ["mariadb:lts has no version to check"]


def test_a_limit_naming_an_image_the_stack_lacks_is_reported():
    stack_ = limited("postgres:18.0", max="12.3")
    problems = stack.image_limit_problems(stack_, stack._services(stack_))
    assert problems == ["limit on mariadb matches no image"]


def test_an_image_name_is_read_past_its_registry_and_tag():
    assert stack._image_name("docker.io/library/mariadb:11.4.13") == "mariadb"
    assert stack._image_name("mariadb@sha256:abc") == "mariadb"


def test_a_v_prefixed_tag_is_still_a_version():
    assert stack._version("v3.3.6") == (3, 3)


def test_dependabot_never_proposes_a_major_image():
    """Auto-merge takes any green Dependabot PR, and no test here starts a container."""
    config = yaml.safe_load((REPO / ".github/dependabot.yml").read_text(encoding="utf-8"))
    entry = next(u for u in config["updates"] if u["package-ecosystem"] == "docker-compose")
    rule = next(i for i in entry["ignore"] if i["dependency-name"] == "*")
    assert rule["update-types"] == ["version-update:semver-major"]


def test_check_stacks_reports_across_stacks_too():
    stacks = {"a": web("a"), "b": web("b")}
    findings = stack.check_stacks(stacks, CLEAN_ENV)
    assert "host port claimed twice: 8080/tcp: a, b" in findings
    assert "service defined twice: web: a, b" in findings
