# TODO — Homelab

## When the PCs exist

- [ ] Follow `docs/deploying.md`. No container has ever been started: expect fixes
- [ ] Put the real IPs and machine count in `inventory.toml`
- [ ] Test a restore from `backup-server`
- [ ] Add an off-site copy of the backups
- [ ] Move the always-on jobs here from the workstation: the `social-scraper` collectors and
      `data-lake`. Those live in their own repositories, so it is a change there
- [ ] Point a workflow at the `ci` runner with `runs-on: [self-hosted, homelab]`
- [ ] Keep the two Pi-holes' blocklists in step: only their `home.arpa` setup is shared

## Archive

- [x] `DEVKIT_VERSION` stamped; `DEVKIT_DIR` set by the PR gate's drift job
- [x] Replace the placeholder in `homelab/` with something that does the job
- [x] Delete `tests/test_smoke.py` once real tests exist
