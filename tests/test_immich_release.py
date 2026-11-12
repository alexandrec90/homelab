"""`scripts/immich-release.py`: Immich tracked by its release, not its registry tags."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]


def load_script() -> ModuleType:
    path = REPO / "scripts" / "immich-release.py"
    spec = importlib.util.spec_from_file_location("immich_release", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["immich_release"] = module  # dataclasses reads its module's annotations here
    spec.loader.exec_module(module)
    return module


ir = load_script()

DB = "ghcr.io/immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0@sha256:" + "b" * 64
NEW_DB = "ghcr.io/immich-app/postgres:14-vectorchord0.5.0-pgvectors0.2.0@sha256:" + "c" * 64


def stack_compose(tag: str = "v3.3.6", ml_tag: str | None = None, db: str = DB) -> str:
    return (
        f"# Immich, from its {tag} release compose. Prefixed names.\n"
        "services:\n"
        "  immich-server:\n"
        f"    image: ghcr.io/immich-app/immich-server:{tag}\n"
        "  immich-ml:\n"
        f"    image: ghcr.io/immich-app/immich-machine-learning:{ml_tag or tag}\n"
        "  immich-redis:\n"
        "    image: docker.io/valkey/valkey:9@sha256:" + "a" * 64 + "\n"
        "  immich-db:\n"
        f"    image: {db}\n"
    )


def release_compose(db: str = NEW_DB) -> str:
    return (
        "services:\n"
        "  immich-server:\n"
        "    image: ghcr.io/immich-app/immich-server:${IMMICH_VERSION:-release}\n"
        "  immich-machine-learning:\n"
        "    # image: ghcr.io/immich-app/immich-machine-learning:${IMMICH_VERSION:-release}-cuda\n"
        "    image: ghcr.io/immich-app/immich-machine-learning:${IMMICH_VERSION:-release}\n"
        "  database:\n"
        f"    image: {db}\n"
    )


# --- the cause: Dependabot pages through every tag ---------------------------------


def test_dependabot_leaves_the_immich_images_to_this_script():
    """GHCR answered 429 to Dependabot paging through Immich's tags (run 43007495019)."""
    config = yaml.safe_load((REPO / ".github/dependabot.yml").read_text(encoding="utf-8"))
    entry = next(u for u in config["updates"] if u["package-ecosystem"] == "docker-compose")
    ignored = {rule["dependency-name"] for rule in entry["ignore"] if "update-types" not in rule}
    for name in ir.IMAGES:
        assert f"*{name}" in ignored, f"Dependabot still lists every tag of {name}"


def test_a_scheduled_workflow_runs_the_report():
    """Without it, a release with security fixes would wait for someone to look."""
    workflow = (REPO / ".github/workflows/immich-release.yml").read_text(encoding="utf-8")
    assert "schedule:" in workflow
    assert "python scripts/immich-release.py\n" in workflow


def test_the_photos_stack_is_in_the_shape_the_script_reads():
    compose = ir.COMPOSE.read_text(encoding="utf-8")
    ir.version(ir.pinned_version(compose))
    assert ir._HEADER.search(compose), "the header no longer names the release it came from"


# --- reading versions and images -----------------------------------------------------


def test_a_release_version_reads_as_numbers():
    assert ir.version("v3.4.1") == (3, 4, 1)
    assert ir.version("3.10.0") > ir.version("3.9.9")


@pytest.mark.parametrize("tag", ["release", "v3.4.0-rc.3", "v3.4", ""])
def test_anything_but_a_release_version_is_refused(tag):
    with pytest.raises(ir.ReleaseError):
        ir.version(tag)


def test_images_are_read_by_registry_name():
    found = ir.images(stack_compose())
    assert found[ir.SERVER] == "ghcr.io/immich-app/immich-server:v3.3.6"
    assert found[ir.DATABASE] == DB


def test_a_missing_image_is_named():
    with pytest.raises(ir.ReleaseError, match="immich-app/postgres"):
        ir.images(stack_compose().replace(DB, "postgres:16"))


def test_the_pinned_version_is_the_server_tag():
    assert ir.pinned_version(stack_compose("v3.3.6")) == "v3.3.6"


def test_server_and_machine_learning_on_different_releases_is_refused():
    with pytest.raises(ir.ReleaseError, match="machine learning"):
        ir.pinned_version(stack_compose("v3.3.6", ml_tag="v3.3.5"))


def test_a_pinned_tag_that_is_not_a_release_is_refused():
    with pytest.raises(ir.ReleaseError):
        ir.pinned_version(stack_compose("release"))


def test_a_release_compose_gets_its_version_filled_in():
    found = ir.release_images(release_compose(), "v3.4.1")
    assert found[ir.SERVER] == "ghcr.io/immich-app/immich-server:v3.4.1"
    assert found[ir.ML] == "ghcr.io/immich-app/immich-machine-learning:v3.4.1"
    assert found[ir.DATABASE] == NEW_DB


def test_a_release_compose_with_a_fixed_server_tag_is_refused():
    text = release_compose().replace("${IMMICH_VERSION:-release}", "release")
    with pytest.raises(ir.ReleaseError, match="not tagged"):
        ir.release_images(text, "v3.4.1")


# --- planning, rewriting and reporting -------------------------------------------------


def test_a_plan_compares_versions_as_numbers_not_text():
    chosen = ir.Plan(pinned="v3.9.0", latest="v3.10.0", images={})
    assert chosen.waiting
    assert not chosen.major


def test_a_newer_patch_is_waiting_and_not_major():
    chosen = ir.plan(stack_compose("v3.3.6"), "v3.4.1", release_compose())
    assert chosen.waiting
    assert not chosen.major


def test_the_same_release_is_not_waiting():
    chosen = ir.plan(stack_compose("v3.4.1"), "v3.4.1", release_compose())
    assert not chosen.waiting


def test_a_new_major_is_flagged():
    assert ir.plan(stack_compose("v3.4.1"), "v4.0.0", release_compose()).major


def test_rewrite_moves_all_three_images_and_the_header_together():
    compose = stack_compose("v3.3.6")
    updated = ir.rewrite(compose, ir.plan(compose, "v3.4.1", release_compose()))
    assert updated == stack_compose("v3.4.1", db=NEW_DB)


def test_rewrite_leaves_other_images_alone():
    compose = stack_compose("v3.3.6")
    updated = ir.rewrite(compose, ir.plan(compose, "v3.4.1", release_compose()))
    assert "valkey/valkey:9@sha256:" + "a" * 64 in updated


def test_the_report_is_green_when_up_to_date():
    text, code = ir.report(
        ir.plan(stack_compose("v3.4.1"), "v3.4.1", release_compose()), wrote=False
    )
    assert code == 0
    assert "Up to date." in text


def test_the_report_is_red_while_a_minor_waits():
    text, code = ir.report(ir.plan(stack_compose(), "v3.4.1", release_compose()), wrote=False)
    assert code == 1
    assert "--write" in text
    assert "releases/tag/v3.4.1" in text


def test_a_waiting_major_is_reported_but_green():
    """Dependabot never proposed a major either; it is a person's call."""
    text, code = ir.report(
        ir.plan(stack_compose("v3.4.1"), "v4.0.0", release_compose()), wrote=False
    )
    assert code == 0
    assert "--major" in text


def test_the_report_after_a_write_lists_the_images():
    text, code = ir.report(ir.plan(stack_compose(), "v3.4.1", release_compose()), wrote=True)
    assert code == 0
    assert NEW_DB in text


# --- fetching and the command line -----------------------------------------------------


def test_fetch_latest_reads_the_release_and_its_compose_asset(monkeypatch):
    asset_url = "https://example.invalid/docker-compose.yml"
    release = {
        "tag_name": "v3.4.1",
        "assets": [{"name": "docker-compose.yml", "browser_download_url": asset_url}],
    }
    bodies = {
        f"{ir.API}/releases/latest": json.dumps(release).encode(),
        asset_url: release_compose().encode(),
    }
    monkeypatch.setattr(ir, "_get", lambda url, accept: bodies[url])
    assert ir.fetch_latest() == ("v3.4.1", release_compose())


def test_a_release_without_a_compose_asset_is_refused(monkeypatch):
    monkeypatch.setattr(
        ir, "_get", lambda url, accept: json.dumps({"tag_name": "v3.4.1", "assets": []}).encode()
    )
    with pytest.raises(ir.ReleaseError, match=r"no docker-compose\.yml"):
        ir.fetch_latest()


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    compose = tmp_path / "compose.yaml"
    compose.write_text(stack_compose("v3.3.6"), encoding="utf-8")
    monkeypatch.setattr(ir, "COMPOSE", compose)
    monkeypatch.setattr(ir, "ARTIFACT", tmp_path / "logs" / "immich-release.md")
    return compose


def latest(monkeypatch, tag):
    monkeypatch.setattr(ir, "fetch_latest", lambda: (tag, release_compose()))


def test_main_reports_without_writing(sandbox, monkeypatch):
    latest(monkeypatch, "v3.4.1")
    assert ir.main([]) == 1
    assert sandbox.read_text(encoding="utf-8") == stack_compose("v3.3.6")
    assert "waiting" in ir.ARTIFACT.read_text(encoding="utf-8")


def test_main_writes_a_minor(sandbox, monkeypatch):
    latest(monkeypatch, "v3.4.1")
    assert ir.main(["--write"]) == 0
    assert sandbox.read_text(encoding="utf-8") == stack_compose("v3.4.1", db=NEW_DB)


def test_main_writes_no_major_unless_asked(sandbox, monkeypatch):
    latest(monkeypatch, "v4.0.0")
    assert ir.main(["--write"]) == 0
    assert sandbox.read_text(encoding="utf-8") == stack_compose("v3.3.6")
    assert ir.main(["--write", "--major"]) == 0
    assert "immich-server:v4.0.0" in sandbox.read_text(encoding="utf-8")


def test_main_writes_the_failure_to_the_artifact(sandbox, monkeypatch):
    def broken():
        raise OSError("network is down")

    monkeypatch.setattr(ir, "fetch_latest", broken)
    assert ir.main([]) == 2
    assert "network is down" in ir.ARTIFACT.read_text(encoding="utf-8")
