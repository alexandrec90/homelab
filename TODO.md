# TODO — Homelab

## When the host exists

- [ ] Pick the host and follow "Deploying to a host" in `README.md`
- [ ] Fill in `.env` on the host from `.env.example`
- [ ] First real start: `docker compose config` has been checked, but no service in
      `compose.yaml` has been run yet
- [ ] Decide on remote access (VPN) and on HTTPS for Nextcloud

## Archive

- [x] `DEVKIT_VERSION` stamped; `DEVKIT_DIR` set by the PR gate's drift job
- [x] Replace the placeholder in `homelab/` with something that does the job
- [x] Delete `tests/test_smoke.py` once real tests exist
