"""Bringing a machine in line, with Docker and SSH replaced by a recorder."""

import socket
import sys

import pytest
from support import Recorder, project

from homelab import deploy
from homelab.fleet import Fleet, FleetError, Machine, Route, Stack
from homelab.render import HostIds

IDS = HostIds(1000, 1000, 44)


def test_run_in_returns_the_exit_code(tmp_path):
    run = deploy.run_in(tmp_path)
    assert run([sys.executable, "-c", "raise SystemExit(3)"]) == 3


def test_render_group_id_prefers_render_then_video():
    def only_video(name):
        if name == "video":
            return 44
        raise KeyError(name)

    assert deploy.render_group_id(only_video) == 44
    assert deploy.render_group_id(lambda _: 109) == 109


def test_render_group_id_is_zero_with_neither_group():
    def nothing(name):
        raise KeyError(name)

    assert deploy.render_group_id(nothing) == 0


def test_host_ids_returns_ids():
    assert isinstance(deploy.host_ids(), HostIds)


def test_resolve_machine_uses_the_hostname_by_default(monkeypatch):
    fleet = Fleet("lan", (Machine("box-1", "1", 1, ()),), {})
    monkeypatch.setattr(socket, "gethostname", lambda: "Box-1.local")
    assert deploy.resolve_machine(fleet, None).name == "box-1"


def test_resolve_machine_refuses_an_unknown_hostname(monkeypatch):
    fleet = Fleet("lan", (Machine("box-1", "1", 1, ()),), {})
    monkeypatch.setattr(socket, "gethostname", lambda: "desktop")
    with pytest.raises(FleetError, match="desktop"):
        deploy.resolve_machine(fleet, None)


def test_select_stacks_sorts_by_what_is_set():
    stacks = {
        "ready": Stack("ready", 1, "${A:?x}"),
        "later": Stack("later", 1, "${B:?x}", optional=True),
        "broken": Stack("broken", 1, "${C:?x}"),
    }
    box = Machine("box", "1", 1, ("ready", "later", "broken"))
    chosen = deploy.select_stacks(Fleet("lan", (box,), stacks), box, {"A": "1", "C": ""})
    assert chosen == deploy.Selection(
        active=("ready",), skipped={"later": ("B",)}, missing={"broken": ("C",)}
    )


def test_compose_command_names_only_the_given_stacks():
    command = deploy.compose_command(["a", "b"], ["ps"])
    assert command[:4] == ["docker", "compose", "--project-name", "homelab"]
    assert "stacks/a/compose.yaml" in command
    assert "stacks/b/compose.yaml" in command
    assert command[-1] == "ps"


def test_write_generated_writes_each_file(tmp_path):
    routes = (Route("web", "Web", 8080, "G"),)
    box = Machine("box", "10.0.0.5", 9, ("web",))
    fleet = Fleet("lan", (box,), {"web": Stack("web", 1, routes=routes)})
    deploy.write_generated(tmp_path, fleet, {"A": "1"})
    assert (tmp_path / ".generated/machine.env").read_text(encoding="utf-8") == "A=1\n"
    assert "web.lan" in (tmp_path / ".generated/Caddyfile").read_text(encoding="utf-8")
    assert (tmp_path / ".generated/homepage/services.yaml").is_file()
    assert (tmp_path / ".generated/homepage/settings.yaml").is_file()


def data_fleet():
    stacks = {
        "web": Stack("web", 1, "${DATA_ROOT}/web/a:/x ${MEDIA_ROOT}/music:/y"),
        "home": Stack("home", 1),
        "proxy": Stack("proxy", 1),
    }
    box = Machine("box", "10.0.0.5", 9, ("web", "home", "proxy"))
    return Fleet("lan", (box,), stacks)


def test_prepare_data_creates_mount_points_for_active_stacks(tmp_path):
    env = {"DATA_ROOT": str(tmp_path / "d"), "MEDIA_ROOT": str(tmp_path / "m")}
    deploy.prepare_data(data_fleet(), ["web"], env)
    assert (tmp_path / "d/web/a").is_dir()
    assert (tmp_path / "m/music").is_dir()


def test_prepare_data_seeds_home_assistant_once(tmp_path):
    env = {"DATA_ROOT": str(tmp_path / "d"), "MEDIA_ROOT": str(tmp_path / "m")}
    seeded = tmp_path / "d/home-assistant/configuration.yaml"
    seeded.parent.mkdir(parents=True)
    deploy.prepare_data(data_fleet(), ["home"], env)
    assert "trusted_proxies" in seeded.read_text(encoding="utf-8")
    seeded.write_text("mine", encoding="utf-8")
    deploy.prepare_data(data_fleet(), ["home"], env)
    assert seeded.read_text(encoding="utf-8") == "mine"


def test_load_valid_refuses_a_broken_inventory(tmp_path):
    project(tmp_path, stacks='["nope"]')
    with pytest.raises(FleetError, match="no stack named nope"):
        deploy.load_valid(tmp_path)


def test_prepare_returns_the_selection(tmp_path):
    project(tmp_path)
    selection, fleet = deploy.prepare(tmp_path, "box", IDS)
    assert selection.active == ("web",)
    assert fleet.domain == "lan"
    assert (tmp_path / "data/web").is_dir()


def test_prepare_says_to_run_init_when_there_is_no_env(tmp_path):
    project(tmp_path, env=None)
    with pytest.raises(FleetError, match="homelab init"):
        deploy.prepare(tmp_path, "box", IDS)


def test_prepare_names_the_missing_variables_and_writes_nothing(tmp_path):
    project(tmp_path, env="")
    with pytest.raises(FleetError, match="web: SITE_PASSWORD"):
        deploy.prepare(tmp_path, "box", IDS)
    assert not (tmp_path / ".generated").exists()


def test_deploy_pulls_then_starts_and_removes_what_left(tmp_path):
    project(tmp_path)
    run = Recorder()
    assert deploy.deploy(tmp_path, "box", run, IDS) == 0
    assert [c[-1] for c in run.commands] == ["pull", "--remove-orphans"]


def test_deploy_reloads_the_proxy_when_this_machine_runs_it(tmp_path):
    project(tmp_path, stacks='["web", "proxy"]')
    run = Recorder()
    deploy.deploy(tmp_path, "box", run, IDS)
    assert "reload" in run.commands[-1]
    assert "-T" in run.commands[-1]


def test_deploy_stops_at_the_first_failure(tmp_path):
    project(tmp_path)
    run = Recorder(fail_on=["pull"])
    assert deploy.deploy(tmp_path, "box", run, IDS) == 1
    assert len(run.commands) == 1


def test_deploy_says_what_it_skipped(tmp_path, capsys):
    project(tmp_path, env="")
    toml = tmp_path / "stacks/web/stack.toml"
    toml.write_text("memory_mb = 1\noptional = true\n", encoding="utf-8")
    deploy.deploy(tmp_path, "box", Recorder(), IDS)
    assert "skipping web: set SITE_PASSWORD" in capsys.readouterr().out


def test_compose_passthrough_scopes_to_this_machine(tmp_path):
    project(tmp_path)
    run = Recorder()
    deploy.compose_passthrough(tmp_path, "box", ["logs", "web"], run, IDS)
    assert run.commands[0][-2:] == ["logs", "web"]
    assert "stacks/web/compose.yaml" in run.commands[0]
    assert "stacks/proxy/compose.yaml" not in run.commands[0]


def test_init_env_generates_and_reports_what_is_left(tmp_path):
    (tmp_path / ".env.example").write_text("A_PASSWORD=\nTOKEN=\n", encoding="utf-8")
    assert deploy.init_env(tmp_path).endswith("Still blank: TOKEN")
    assert "A_PASSWORD=\n" not in (tmp_path / ".env").read_text(encoding="utf-8")


def test_init_env_leaves_existing_values_alone(tmp_path):
    (tmp_path / ".env.example").write_text("A_PASSWORD=\n", encoding="utf-8")
    (tmp_path / ".env").write_text("A_PASSWORD=mine\n", encoding="utf-8")
    assert deploy.init_env(tmp_path).endswith("Still blank: nothing")
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "A_PASSWORD=mine\n"


def test_rollout_commands_copy_secrets_then_update_and_deploy():
    box = Machine("box", "10.0.0.5", 9, (), ssh="alex@box", path="srv/homelab")
    copy, update = deploy.rollout_commands(box)
    assert copy == ["scp", ".env", "alex@box:srv/homelab/.env"]
    assert update[:2] == ["ssh", "alex@box"]
    assert update[2] == (
        "cd srv/homelab && git pull --ff-only && python3 -m homelab deploy --machine box"
    )


def test_rollout_commands_default_to_the_address():
    copy, update = deploy.rollout_commands(Machine("box", "10.0.0.5", 9, ()))
    assert copy[-1] == "10.0.0.5:homelab/.env"
    assert update[1] == "10.0.0.5"


def rollout_project(tmp_path):
    project(tmp_path)
    inventory = tmp_path / "inventory.toml"
    text = inventory.read_text(encoding="utf-8")
    second = '\n[machines.two]\naddress = "10.0.0.6"\nmemory_mb = 9\n'
    inventory.write_text(text + second, encoding="utf-8")


def test_rollout_reaches_every_machine_by_default(tmp_path):
    rollout_project(tmp_path)
    run = Recorder()
    assert deploy.rollout(tmp_path, [], run) == 0
    assert {c[1] for c in run.commands if c[0] == "ssh"} == {"10.0.0.5", "10.0.0.6"}


def test_rollout_can_name_one_machine(tmp_path):
    rollout_project(tmp_path)
    run = Recorder()
    deploy.rollout(tmp_path, ["two"], run)
    assert [c[1] for c in run.commands if c[0] == "ssh"] == ["10.0.0.6"]


def test_rollout_carries_on_past_a_failure_and_reports_it(tmp_path, capsys):
    rollout_project(tmp_path)
    run = Recorder(fail_on=["10.0.0.5"])
    assert deploy.rollout(tmp_path, [], run) == 1
    assert any(c[1] == "10.0.0.6" for c in run.commands if c[0] == "ssh")
    assert "failed: box" in capsys.readouterr().out


def test_plan_text_shows_placement_budget_and_addresses():
    routes = (Route("web", "Web", 8080, "G"),)
    box = Machine("box", "10.0.0.5", 9000, ("web",))
    stacks = {"web": Stack("web", 512, routes=routes), "spare": Stack("spare", 1)}
    text = deploy.plan_text(Fleet("lan", (box,), stacks))
    assert "box (10.0.0.5) 512/9000 MB" in text
    assert "not placed: spare" in text
    assert "https://web.lan  -> box" in text
