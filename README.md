# Homelab

A self-hosted homelab that spreads across as many PCs as you have. Code is written on a
development machine and distributed through GitHub; each PC clones this repo and runs
whichever stacks `inventory.toml` gives it.

| Stack | Runs | Address |
| --- | --- | --- |
| `proxy` | Caddy | every name below |
| `dns` | Pi-hole, on two machines | `pihole-<machine>` |
| `homepage` | Homepage, the start page | `dash` |
| `monitoring` | Uptime Kuma, ntfy, Beszel hub | `status`, `ntfy`, `beszel` |
| `agent` | Beszel agent, on every machine | |
| `backup`, `backup-server` | Restic to a central rest-server | |
| `vpn` | Tailscale, on every machine | |
| `media` | Jellyfin, Plex | `jellyfin`, `plex` |
| `audio` | Audiobookshelf, Navidrome | `books`, `music` |
| `photos` | Immich | `photos` |
| `cloud` | Nextcloud, with database, cache and cron | `cloud` |
| `documents` | Paperless-ngx | `docs` |
| `passwords` | Vaultwarden | `vault` |
| `sync` | Syncthing | `sync` |
| `home` | Home Assistant | `ha` |
| `cameras` | Frigate, not placed until there are cameras | `cameras` |
| `apps` | Mealie, Actual | `recipes`, `budget` |
| `ai` | Ollama, Open WebUI | `chat` |
| `ci` | GitHub Actions runner | |
| `minecraft`, `valheim` | Game servers | |

An address is `https://<name>.home.arpa`. The first steps are in
[docs/deploying.md](docs/deploying.md).

## How it spreads

`inventory.toml` is the only file that says what runs where. From it, each deploy works
out the proxy's routes, the dashboard's tiles, Pi-hole's names, and each machine's backup
target, so services find each other wherever they are. To move a service, move its word
between two machines' `stacks` lists:

```bash
python -m homelab plan      # check the layout, and that each machine has the memory
python -m homelab rollout   # copy .env and deploy to every machine, over SSH
```

`rollout` also stops whatever a machine no longer runs. To add a service, create
`stacks/<name>/` with a `compose.yaml` and a `stack.toml`, then place it. Any stack can
share a machine with any other, because `tests/test_stack.py` fails if two ever claim one
host port.

## Development

```bash
uv sync --all-extras          # creates .venv from the committed uv.lock
# no uv? pip install -e ".[dev]" works, but resolves fresh instead of from the lock
pytest
```

The tests need no Docker daemon. `tests/test_stack.py` holds `stacks/` to the rules in
`homelab/stack.py`: pinned image tags, required secrets, every variable documented in
`.env.example`, routes pointing at published ports, and no host port claimed twice.

`python scripts/e2e.py` is the tier that does need Docker, run nightly on the workstation:
Compose accepts every machine, every pinned image is on its registry, and a real `deploy`
of the proxy, publishing no ports, serves every route. It runs in a throwaway copy.

In VS Code, `Ctrl+Shift+B` runs the default build task and the task quick-pick
(`Ctrl+Shift+P` → "Run Task") lists everything else, each with a one-line `detail`
explaining what it costs and what it touches.


## Layout

```text
inventory.toml           which machine runs what
stacks/                  one folder per stack: compose.yaml and stack.toml
homelab/                 `python -m homelab`, standard library only; and the stack rules
docs/deploying.md        setting up a machine
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
`.github/workflows/dependabot-automerge.yml` merges **every** one once the gate passes,
majors included. Nothing waits for a person, so the gate is the only safeguard, and it
starts no container. For images that means two project-owned guards instead: Dependabot
never proposes a major (`ignore` in `.github/dependabot.yml`), and `image_limits` in a
stack's `stack.toml` fails the gate on a version known not to work. Nothing reaches a
machine until someone runs a rollout.

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
