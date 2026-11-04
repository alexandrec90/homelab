"""The rules in `homelab/stack.py`, and the repository's own stack held to them."""

from pathlib import Path

import pytest
import yaml

from homelab import stack

REPO = Path(__file__).resolve().parent.parent

CLEAN_COMPOSE = """
services:
  web:
    image: nginx:1.27.0
    profiles: [media]
    ports:
      - "${WEB_PORT:-8080}:80"
    environment:
      API_PASSWORD: ${API_PASSWORD:?required}
"""
CLEAN_ENV = "WEB_PORT=8080\nAPI_PASSWORD=\n"


def test_the_repository_stack_breaks_no_rule():
    compose = (REPO / "compose.yaml").read_text(encoding="utf-8")
    env = (REPO / ".env.example").read_text(encoding="utf-8")
    assert stack.check_stack(compose, env) == []


def test_the_repository_stack_covers_each_profile():
    compose = (REPO / "compose.yaml").read_text(encoding="utf-8")
    services = yaml.safe_load(compose)["services"]
    profiles = {profile for service in services.values() for profile in service["profiles"]}
    assert profiles == {"media", "cloud", "dns"}


def test_a_clean_stack_has_no_findings():
    assert stack.check_stack(CLEAN_COMPOSE, CLEAN_ENV) == []


def test_a_compose_file_with_no_services_has_no_findings():
    assert stack.check_stack("name: empty\n", "") == []


def test_referenced_vars_records_each_operator_a_name_is_used_with():
    text = "${A} ${A:-x} ${B:?why} ${C-y}"
    assert stack.referenced_vars(text) == {"A": {"", ":-"}, "B": {":?"}, "C": {"-"}}


def test_documented_vars_ignores_a_commented_out_key():
    assert stack.documented_vars("# OFF=1\nON=1\n") == {"ON"}


def test_undocumented_vars_lists_only_what_the_example_lacks():
    compose = "${KNOWN} ${MISSING_B} ${MISSING_A:-x}"
    assert stack.undocumented_vars(compose, "KNOWN=1\n") == ["MISSING_A", "MISSING_B"]


def test_a_variable_missing_from_the_example_is_reported():
    compose = CLEAN_COMPOSE.replace("WEB_PORT", "OTHER_PORT")
    assert "not in .env.example: OTHER_PORT" in stack.check_stack(compose, CLEAN_ENV)


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
    [
        "nginx",
        "nginx:latest",
        "ghcr.io/org/app:stable",
        "registry:5000/app",
        "nextcloud:production",
    ],
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


def test_a_service_with_no_profile_is_reported():
    services = {"on": {"profiles": ["media"]}, "off": {}, "empty": {"profiles": []}}
    assert stack.unprofiled_services(services) == ["empty", "off"]


def test_two_services_on_one_host_port_are_reported():
    services = {"b": {"ports": ["8080:80"]}, "a": {"ports": ["${PORT:-8080}:8080"]}}
    assert stack.host_port_clashes(services) == ["8080/tcp: a, b"]


def test_one_service_may_publish_a_port_on_both_protocols():
    services = {"dns": {"ports": ["${P:-53}:53/tcp", "${P:-53}:53/udp"]}}
    assert stack.host_port_clashes(services) == []


def test_tcp_and_udp_on_one_port_do_not_clash_across_services():
    services = {"a": {"ports": ["53:53/udp"]}, "b": {"ports": ["53:53/tcp"]}}
    assert stack.host_port_clashes(services) == []


def test_a_port_clash_is_found_past_a_bind_address():
    services = {"a": {"ports": ["127.0.0.1:8080:80"]}, "b": {"ports": ["8080:80"]}}
    assert stack.host_port_clashes(services) == ["8080/tcp: a, b"]


def test_a_service_with_no_ports_cannot_clash():
    assert stack.host_port_clashes({"a": {}, "b": {}}) == []
