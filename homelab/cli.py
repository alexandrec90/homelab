"""`python -m homelab`: `plan` and `rollout` from the desk, `deploy` on a machine."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from homelab import deploy
from homelab.deploy import Runner
from homelab.fleet import FleetError, validate_fleet, load_fleet

REPO_ROOT = Path(__file__).resolve().parent.parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="homelab", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan", help="check the inventory and show where everything runs")
    commands.add_parser("init", help="create or top up .env, generating blank secrets")
    up = commands.add_parser("deploy", help="bring this machine in line with the inventory")
    up.add_argument("--machine", help="default: the name matching this hostname")
    scoped = commands.add_parser("compose", help="docker compose, for this machine's stacks")
    scoped.add_argument("--machine", help="default: the name matching this hostname")
    scoped.add_argument("args", nargs=argparse.REMAINDER, help="for example: ps, or logs -f")
    everywhere = commands.add_parser("rollout", help="deploy over SSH, from this machine")
    everywhere.add_argument("machines", nargs="*", help="default: every machine")
    return parser


def plan(root: Path) -> int:
    fleet = load_fleet(root)
    print(deploy.plan_text(fleet))
    problems = validate_fleet(fleet)
    for problem in problems:
        print(f"PROBLEM: {problem}")
    return 1 if problems else 0


def main(
    argv: Sequence[str] | None = None, root: Path = REPO_ROOT, run: Runner | None = None
) -> int:
    args = build_parser().parse_args(argv)
    runner = run or deploy.run_in(root)
    try:
        if args.command == "plan":
            return plan(root)
        if args.command == "init":
            print(deploy.init_env(root))
            return 0
        if args.command == "deploy":
            return deploy.deploy(root, args.machine, runner, deploy.host_ids())
        if args.command == "compose":
            return deploy.compose_passthrough(
                root, args.machine, args.args, runner, deploy.host_ids()
            )
        return deploy.rollout(root, args.machines, runner)
    except FleetError as exc:
        print(f"homelab: {exc}")
        return 1
