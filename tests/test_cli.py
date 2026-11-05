"""`python -m homelab`, end to end, with Docker replaced."""

import pytest
from support import Recorder, project

from homelab import cli


def test_build_parser_requires_a_command():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([])


def test_build_parser_passes_compose_arguments_through():
    args = cli.build_parser().parse_args(["compose", "logs", "-f", "plex"])
    assert args.args == ["logs", "-f", "plex"]


def test_plan_prints_the_layout_of_a_sound_fleet(tmp_path, capsys):
    project(tmp_path)
    assert cli.plan(tmp_path) == 0
    assert "box (10.0.0.5)" in capsys.readouterr().out


def test_plan_fails_and_says_why_on_a_broken_fleet(tmp_path, capsys):
    project(tmp_path, stacks='["nope"]')
    assert cli.plan(tmp_path) == 1
    assert "PROBLEM: box: no stack named nope" in capsys.readouterr().out


def test_main_runs_plan(tmp_path):
    project(tmp_path)
    assert cli.main(["plan"], tmp_path) == 0


def test_main_runs_init(tmp_path, capsys):
    project(tmp_path, env=None)
    (tmp_path / ".env.example").write_text("A_PASSWORD=\n", encoding="utf-8")
    assert cli.main(["init"], tmp_path) == 0
    assert ".env ready" in capsys.readouterr().out


def test_main_runs_deploy(tmp_path):
    project(tmp_path)
    run = Recorder()
    assert cli.main(["deploy", "--machine", "box"], tmp_path, run) == 0
    assert run.commands


def test_main_runs_compose(tmp_path):
    project(tmp_path)
    run = Recorder()
    assert cli.main(["compose", "--machine", "box", "ps"], tmp_path, run) == 0
    assert run.commands[0][-1] == "ps"


def test_main_runs_rollout(tmp_path):
    project(tmp_path)
    run = Recorder()
    assert cli.main(["rollout", "box"], tmp_path, run) == 0
    assert run.commands[0][0] == "scp"


def test_main_turns_a_fleet_error_into_a_message_not_a_traceback(tmp_path, capsys):
    project(tmp_path)
    assert cli.main(["deploy", "--machine", "ghost"], tmp_path, Recorder()) == 1
    assert "no machine named 'ghost'" in capsys.readouterr().out
