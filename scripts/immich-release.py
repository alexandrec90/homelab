"""Track Immich by its GitHub release, which Dependabot cannot do.

Dependabot learns an image's versions by listing every tag in its registry, and Immich
pushes a tag for every pull-request build. Its two GHCR repositories hold so many that
one weekly run spent about 5,000 requests paging through them, and the next was
refused by GHCR with 429 (`registry_error` on immich-server and
immich-machine-learning, run 43007495019). So `.github/dependabot.yml` ignores both,
and this script does their job from a single API call.

A release is also the better source. Its `docker-compose.yml` names the database image
that release was built for, so all three images move together, which a tag bump can
never arrange.

    python scripts/immich-release.py            # report; exit 1 while a release is waiting
    python scripts/immich-release.py --write    # adopt the newest minor or patch release
    python scripts/immich-release.py --write --major

A major is reported but never adopted without `--major`, for the same reason Dependabot
never proposes one: no test starts a container. Read its release notes first.

`.github/workflows/immich-release.yml` runs the report weekly, so a waiting release
shows up as a red scheduled run, and through it as an issue. Every run writes
`logs/immich-release.md`. Standard library only, like everything under `scripts/`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
COMPOSE = REPO / "stacks" / "photos" / "compose.yaml"
ARTIFACT = REPO / "logs" / "immich-release.md"
API = "https://api.github.com/repos/immich-app/immich"

# Each image this script moves, by its name in the registry.
SERVER = "immich-app/immich-server"
ML = "immich-app/immich-machine-learning"
DATABASE = "immich-app/postgres"
IMAGES = (SERVER, ML, DATABASE)

_IMAGE_LINE = re.compile(
    r"^(?P<lead>\s*image:\s*)(?P<ref>\S*/(?P<name>immich-app/[\w.-]+)[:@]\S*)$", re.M
)
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_HEADER = re.compile(r"(from its )v\d+\.\d+\.\d+( release compose)")


class ReleaseError(Exception):
    """The release, or this stack's compose, is not in the shape this script reads."""


@dataclass(frozen=True)
class Plan:
    """What adopting a release would change."""

    pinned: str
    latest: str
    images: dict[str, str]

    @property
    def waiting(self) -> bool:
        return version(self.latest) > version(self.pinned)

    @property
    def major(self) -> bool:
        return version(self.latest)[0] > version(self.pinned)[0]


def version(tag: str) -> tuple[int, int, int]:
    """`v3.4.1` as `(3, 4, 1)`; anything else is an error, never a guess."""
    match = _VERSION.match(tag)
    if not match:
        raise ReleaseError(f"{tag!r} is not a release version")
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def images(compose: str) -> dict[str, str]:
    """The full image reference of each Immich image a compose file names."""
    found = {m["name"]: m["ref"] for m in _IMAGE_LINE.finditer(compose)}
    missing = [name for name in IMAGES if name not in found]
    if missing:
        raise ReleaseError(f"no image line for {', '.join(missing)}")
    return {name: found[name] for name in IMAGES}


def pinned_version(compose: str) -> str:
    """The release this stack runs, read from the server image's tag."""
    found = images(compose)
    server_tag, ml_tag = (_tag(found[name]) for name in (SERVER, ML))
    version(server_tag)
    if ml_tag != server_tag:
        raise ReleaseError(f"server is {server_tag} but machine learning is {ml_tag}")
    return server_tag


def _tag(ref: str) -> str:
    return ref.split("@", 1)[0].rsplit(":", 1)[-1]


def release_images(release_compose: str, tag: str) -> dict[str, str]:
    """The images a release's own compose names, with its version filled in.

    Immich's compose leaves the server and machine-learning tags to `IMMICH_VERSION`,
    so they are the release tag. The database image it names outright.
    """
    named = images(release_compose)
    filled = {name: re.sub(r"\$\{IMMICH_VERSION[^}]*\}", tag, ref) for name, ref in named.items()}
    for name in (SERVER, ML):
        if not filled[name].endswith(f":{tag}"):
            raise ReleaseError(f"{name} in release {tag} is {filled[name]}, not tagged {tag}")
    return filled


def plan(compose: str, latest: str, release_compose: str) -> Plan:
    return Plan(pinned_version(compose), latest, release_images(release_compose, latest))


def rewrite(compose: str, chosen: Plan) -> str:
    """The compose file with the release's images and its header naming the release."""

    def swap(match: re.Match[str]) -> str:
        return match["lead"] + chosen.images[match["name"]]

    updated = _IMAGE_LINE.sub(lambda m: swap(m) if m["name"] in chosen.images else m[0], compose)
    return _HEADER.sub(rf"\g<1>{chosen.latest}\g<2>", updated)


def report(chosen: Plan, *, wrote: bool) -> tuple[str, int]:
    """The artifact's Markdown and the exit code for one run."""
    lines = ["# Immich release", "", f"- pinned: {chosen.pinned}", f"- latest: {chosen.latest}"]
    if not chosen.waiting:
        return "\n".join([*lines, "", "Up to date."]) + "\n", 0
    notes = f"https://github.com/immich-app/immich/releases/tag/{chosen.latest}"
    lines += ["", f"Release notes: {notes}", ""]
    if wrote:
        lines.append(f"Wrote {chosen.latest} into stacks/photos/compose.yaml:")
        lines += [f"- `{ref}`" for ref in chosen.images.values()]
        return "\n".join(lines) + "\n", 0
    if chosen.major:
        lines.append(
            f"{chosen.latest} is a major release. Read its notes, then run "
            "`python scripts/immich-release.py --write --major`."
        )
        return "\n".join(lines) + "\n", 0
    lines.append(f"{chosen.latest} is waiting. Run `python scripts/immich-release.py --write`.")
    return "\n".join(lines) + "\n", 1


def _get(url: str, accept: str) -> bytes:
    headers = {"Accept": accept, "User-Agent": "homelab-immich-release"}
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    # Only the API gets the token. An asset download redirects to a signed storage URL,
    # and urllib carries the header across the redirect, where a second credential is
    # refused.
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def fetch_latest() -> tuple[str, str]:
    """The newest non-prerelease tag and that release's `docker-compose.yml`."""
    release = json.loads(_get(f"{API}/releases/latest", "application/vnd.github+json"))
    tag = release["tag_name"]
    asset = next((a for a in release.get("assets", []) if a["name"] == "docker-compose.yml"), None)
    if asset is None:
        raise ReleaseError(f"release {tag} has no docker-compose.yml asset")
    compose = _get(asset["browser_download_url"], "application/octet-stream").decode("utf-8")
    return tag, compose


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--write", action="store_true", help="adopt the newest release")
    parser.add_argument("--major", action="store_true", help="with --write, allow a major")
    args = parser.parse_args(argv)
    ARTIFACT.parent.mkdir(exist_ok=True)
    try:
        compose = COMPOSE.read_text(encoding="utf-8")
        chosen = plan(compose, *fetch_latest())
    except (ReleaseError, OSError, KeyError, ValueError) as error:
        ARTIFACT.write_text(f"# Immich release\n\nCould not check: {error}\n", encoding="utf-8")
        print(f"immich-release: could not check ({error}); see {ARTIFACT}")
        return 2
    wrote = args.write and chosen.waiting and (args.major or not chosen.major)
    if wrote:
        COMPOSE.write_text(rewrite(compose, chosen), encoding="utf-8", newline="\n")
    text, code = report(chosen, wrote=wrote)
    ARTIFACT.write_text(text, encoding="utf-8")
    print(f"immich-release: pinned {chosen.pinned}, latest {chosen.latest}; see {ARTIFACT}")
    return code


if __name__ == "__main__":
    sys.exit(main())
