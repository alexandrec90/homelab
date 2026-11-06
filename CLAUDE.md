# Homelab

A self-hosted homelab spread across several PCs. `inventory.toml` places the stacks in
`stacks/` on machines; `python -m homelab` derives everything else from that.

**This machine is not a host.** Code is written here and distributed through GitHub;
other PCs clone the repo and run the stacks. Do not bring a stack up here to verify a
change: `docker compose config` and `tests/test_stack.py` are the checks, and Pi-hole's
port 53 would collide with this workstation's own DNS. `scripts/e2e.py` is the one
exception, and it stays one by publishing no port and working in a throwaway copy.

**`python -m homelab` runs on hosts with no virtualenv.** Everything it imports is
standard library. Only `homelab/stack.py` imports PyYAML, so nothing else may import it.

**Host ports are unique across every stack, placed or not.** That is what lets any stack
share a machine with any other, so pick a free port rather than reuse one.

**A green gate says nothing about whether images work together**, because no test starts a
container, and `dependabot-automerge.yml` merges every green Dependabot PR, majors included.
When two images have a known version relationship, record it as `image_limits` in the
stack's `stack.toml`: that is what makes the gate able to refuse it.

## Tech Stack

| Layer | Choice |
| --- | --- |
| Stacks | Docker Compose, one file per stack |
| Language | Python 3.12 here, 3.11 or newer on a host |
| Tests | pytest |
| Lint | ruff |

## Environment Variables

See `.env.example` for every variable. `.env` is gitignored, holds the credentials, and is
the same on every machine. A new variable in a stack goes in `.env.example` in the same
change, or in `render.GENERATED_VARS` if the inventory decides it; `tests/test_stack.py`
fails until it does.

## Tooling

> Everything in this section needs the local Docker Desktop daemon. If it isn't
> running, make the code change and defer container/stack verification until it is
> (or to CI). Run `docker ps` first — an `npipe`/daemon error means Desktop is stopped.

### Scripts and the vendored harness

Both are covered by **`.claude/rules/engineering.md`** — script conventions (pure
importable functions, stdlib-only hooks, tests in the same change), the failure-artifact
rule, and how the `.devkit.toml` seam works. That file is vendored from
[devkit](https://github.com/alexandrec90/devkit) and drift-gated, so it is the
authority; this file does not repeat it.

### VS Code tasks

- Use `"type": "process"` so VS Code monitors the process directly — that is what
  makes the spinner stop and the exit-code icon appear reliably.
- Set `"close": false` in `presentation` so the terminal stays open for review.
- **Wrap with `notify-wrap.py`** for the completion toast; never call `notify.py`
  from inside a script. Notifications are a task-layer concern only.
- Label convention: `"Domain: Title Case Action"`, and **every task carries a
  `detail`** — that is the second line in the quick-pick, and the only place a
  one-click action can state its cost or blast radius.

### Failure artifacts (fix from a file, not from the terminal)

Any task or script whose failures an agent is expected to act on must persist the
failure to a **parseable artifact file** under `logs/`. Never rely on streamed
terminal output — it scrolls away and buries the signal. Keep the terminal to a
status line plus the artifact path; put everything needed to diagnose in the file.
Write the artifact on failure too, not just success, and overwrite per run.

### Docker subprocess calls

- **`docker compose exec` must use `-T`** — without it a pseudo-TTY is allocated and
  the subprocess handle can outlive the command, leaving the caller hung.

## Testing

**`.claude/rules/engineering.md`** is the authority: tests ship in the same commit,
every testable unit of logic is covered, regression test first, targeted runs locally
and full runs in CI.

Add this project's specifics *below* — fixtures, isolation rules, markers, what to mock
and where — but do not restate the policy above. It is vendored and drift-gated; a copy
here is a fork that will disagree with it the first time either is edited.

A test that needs a Docker daemon or the registries is marked `local`, which `addopts`
deselects and `scripts/e2e.py` selects (`.claude/rules/local-e2e.md`). They live in
`tests/test_e2e_stacks.py` and drive a copy of the checkout, never this one. A deploy
always uses the project name `homelab` and removes its orphans, so the one test that
deploys fails rather than run on a machine that already has that project.

## Guardrails

Baseline guardrails — including the instruction-file feedback loop (**never silently
work around a bad instruction**) — are in `.claude/rules/engineering.md`. Rules for
writing skills and rules themselves are in `.claude/rules/authoring.md`. Cross-reference
this project's own scoped rules here, one line each.
