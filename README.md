# Homelab

Self-hosted media, cloud storage and DNS as one Docker Compose stack. The code is written
on a development machine and distributed through GitHub; a separate host clones this
repo and runs it.

| Profile | Services | Default port |
| --- | --- | --- |
| `media` | Jellyfin | 8096 |
| `media` | Plex | 32400 |
| `cloud` | Nextcloud, with MariaDB, Redis and a cron worker | 8080 |
| `dns` | Pi-hole | 53 (DNS), 8053 (admin at `/admin`) |

Generated from [devkit](https://github.com/alexandrec90/devkit)'s project
template. The agent harness in `scripts/hooks/` is vendored from there — see
`CLAUDE.md`, "Vendored agent harness".

## Deploying to a host

The host needs Docker with the Compose plugin, and git. Python is not required.

```bash
git clone https://github.com/alexandrec90/homelab.git && cd homelab
cp .env.example .env          # set the passwords, TZ, DATA_ROOT, MEDIA_ROOT
mkdir -p data/jellyfin/config data/jellyfin/cache media   # or your DATA_ROOT and MEDIA_ROOT
docker compose up -d
```

`COMPOSE_PROFILES` in `.env` picks the groups this machine runs, so adding a service is a
matter of adding it to `compose.yaml` and to a profile. To update:

```bash
git pull && docker compose pull && docker compose up -d
```

Before relying on it:

- **Pi-hole needs port 53.** On Ubuntu, `systemd-resolved` already holds it; set
  `DNSStubListener=no` in `/etc/systemd/resolved.conf` and restart that service first.
  Then point your router's DNS at the host.
- **Plex:** set `PLEX_CLAIM` before the first start, with a token from
  <https://plex.tv/claim>; it expires in minutes.
- **Hardware transcoding** depends on the host's GPU. Add the `devices:` entries to a
  gitignored `compose.override.yaml`, which Compose reads automatically.
- **Do not forward these ports to the internet.** They serve plain HTTP on the LAN. Use a
  VPN such as Tailscale for remote access.
- **Back up `DATA_ROOT`.** It holds everything the services write, the Nextcloud database
  included.

## Development

```bash
uv sync --all-extras          # creates .venv from the committed uv.lock
# no uv? pip install -e ".[dev]" works, but resolves fresh instead of from the lock
pytest
```

The tests need no Docker daemon: `tests/test_stack.py` holds `compose.yaml` to the rules
in `homelab/stack.py`, which are pinned image tags, required secrets, every variable
documented in `.env.example`, a profile on every service, and no host port published
twice. `docker compose config` is the check for Compose's own syntax.

In VS Code, `Ctrl+Shift+B` runs the default build task and the task quick-pick
(`Ctrl+Shift+P` → "Run Task") lists everything else, each with a one-line `detail`
explaining what it costs and what it touches.


## Layout

```text
compose.yaml             the stack
homelab/                 rules the stack is held to
tests/                   tests
scripts/                 project scripts (Python, each with tests)
scripts/hooks/           vendored agent harness — edit upstream in devkit
.devkit.toml      the per-project harness seam (NOT vendored)
```

## CI

`.github/workflows/pr-gate.yml` runs lint, tests, and the harness drift check on
every PR. The drift check is only meaningful when it can see devkit — if it prints
"nothing to do (skipping)", the gate is inert and the wiring needs fixing. Locally,
where `DEVKIT_DIR` is usually unset, `pre-commit run devkit-drift --all-files` is the
same check against the devkit rev pinned in `.pre-commit-config.yaml`.

`.github/dependabot.yml` opens weekly dependency PRs, and
`.github/workflows/dependabot-automerge.yml` merges them once the gate passes —
patch/minor bumps of anything, plus majors confined to dev tooling. A major that
touches a runtime dependency is labelled `needs-manual-merge` and waits for you.

That auto-merge workflow is **vendored** from devkit, so `sync-devkit.py --check`
compares it byte-for-byte and editing it here shows up as drift. Keep the gate's title
`PR Gate`: the auto-merge workflow waits on it by name, and a rename makes the merge job
inert without turning anything red.

`.github/actions/setup-python-env/` is **yours**, by contrast. devkit shipped it through
that same channel for one release and pulled it back out when two projects turned out to
install in ways `uv sync` cannot express, so it now arrives from `templates/` as a
one-shot copy: nothing compares it, and rewriting its steps to match how this project
installs is the supported move rather than drift. What owning it costs is the other
direction — later devkit fixes to that file never reach here.
