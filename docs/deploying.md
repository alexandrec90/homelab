# Deploying

No stack has been run yet. Everything below was checked against each project's
documentation and with `docker compose config`, not on a real machine, so expect the first
start to need fixes. The order that fails least painfully is one PC first, with `proxy`,
`dns`, `homepage` and `monitoring`, then the rest.

## Once, on your desk

1. Edit `inventory.toml`: the real IPs, and the stacks for however many PCs you have.
   `python -m homelab plan` tells you whether each PC can hold what it was given.
2. Run `python -m homelab init`. It creates `.env` with a generated value for every
   password, and lists what you still have to fill in yourself.
3. Store `RESTIC_PASSWORD` somewhere that is not one of these PCs. It is the only way to
   read the backups.
4. Commit and push `inventory.toml`, since machines read it from GitHub.

## Each PC

Ubuntu Server 24.04 LTS is what this is written for. Anything with Docker and Python 3.11
or newer should do.

1. During install, set the hostname to the machine's name from `inventory.toml`, turn on
   OpenSSH, and create the user whose SSH login you will use.
2. Reserve its IP on your router, to match the inventory.
3. Install Docker, and let your user run it:

   ```bash
   curl -fsSL https://get.docker.com | sh
   sudo usermod -aG docker "$USER"   # then log out and back in
   ```

4. Clone the repository into your home directory. It is private, so the PC needs a way in:
   a [deploy key](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys)
   is the narrowest, being read-only and limited to this repository.

   ```bash
   git clone git@github.com:alexandrec90/homelab.git
   ```

5. If it runs `dns`, free port 53, which Ubuntu's own resolver holds:

   ```bash
   echo 'DNSStubListener=no' | sudo tee -a /etc/systemd/resolved.conf
   sudo systemctl restart systemd-resolved
   ```

6. From your desk, with your SSH key on the PC (`ssh-copy-id`):

   ```bash
   python -m homelab rollout box-1
   ```

## After the first deploy

- **DNS:** give your router the two Pi-hole machines' addresses as its DNS servers, so
  every device gets the `home.arpa` names and ad blocking.
- **Trust the certificates:** Caddy signs them with its own authority, so browsers warn
  until each device trusts it. The certificate is on the `proxy` machine at
  `data/caddy/data/caddy/pki/authorities/local/root.crt`. Vaultwarden and Actual need this,
  since they only work over HTTPS that the browser accepts.
- **Monitoring:** open `https://beszel.home.arpa`, make an account, and copy the key and
  token from Add System into `BESZEL_KEY` and `BESZEL_TOKEN`. Then roll out, and every
  machine's agent reports in by itself. In `https://status.home.arpa`, add a monitor for
  each address `plan` lists, and ntfy as the notification: Uptime Kuma has no config file,
  so this part is by hand.
- **Vaultwarden:** create your account, set `VAULTWARDEN_SIGNUPS_ALLOWED=false`, and roll
  out again.
- **Plex:** put a fresh token from <https://plex.tv/claim> in `PLEX_CLAIM` right before its
  first start.
- **Transcoding:** turn on Intel Quick Sync in Jellyfin's and Plex's playback settings. This
  CPU handles H.264 and 8-bit HEVC in hardware, but not AV1 or most 10-bit HEVC. Plex needs a
  Plex Pass for it.
- **Remote access:** with `TAILSCALE_AUTHKEY` set, every machine joins your tailnet. To use
  the `home.arpa` names away from home, set `tailscale_routes` on one machine, approve the
  route in the Tailscale console, and add Pi-hole as the tailnet's DNS for `home.arpa`.

## Everyday commands

Run on a machine, from the checkout:

```bash
python3 -m homelab deploy               # bring this machine in line with the inventory
python3 -m homelab compose ps           # what is running
python3 -m homelab compose logs -f plex
```

## Moving a stack

Rollout starts a stack on its new machine and stops it on the old one, but the data does not
follow. Without this, the service comes up empty on the new machine:

1. Stop it on the old machine: `python3 -m homelab compose stop <services>`.
2. Copy its folders, keeping ownership. For `passwords`, as an example:

   ```bash
   sudo rsync -a data/vaultwarden/ newbox:homelab/data/vaultwarden/
   ```

3. Move its word in `inventory.toml`, push, and `python -m homelab rollout`.
4. Check it works before deleting the old copy.

## Updates

Minor and patch image updates arrive on their own: Dependabot opens them weekly, they merge
when the gate passes, and they reach a machine at your next rollout. Read what changed
first, since the gate only checks that the files are well-formed.

**Major updates never arrive on their own**, so check for them now and then. Do one at a
time, and only after confirming the machine's backup is recent:

- **Nextcloud** goes up one major at a time, and skipping one is not supported. Before each,
  look up the database versions the new major supports, and update `image_limits` in
  `stacks/cloud/stack.toml`.
- **MariaDB** must stay inside those limits. `MARIADB_AUTO_UPGRADE` converts the data files
  at startup, so the way back from a bad upgrade is a restore.
- **Immich** is not updated by Dependabot. Its registry holds too many tags for Dependabot
  to list. The weekly `Immich release` workflow goes red when a release is waiting. Read
  its release notes, which list any required steps, then run
  `python scripts/immich-release.py --write`. That moves the server, machine-learning and
  database images together, to what the release's own `docker-compose.yml` names. A major
  also needs `--major`.

## Backups

Each machine's `DATA_ROOT` goes to the `backup-server` stack at 03:30, into a repository of
its own, encrypted, keeping 7 daily, 4 weekly and 6 monthly copies.

- **What is not covered:** `MEDIA_ROOT`. A 1 TB backup PC cannot hold every other PC's
  library, and media can be replaced. Thumbnails, caches, models and recordings are skipped
  for the same reason. Watch the backup PC's disk in Beszel.
- **Nothing is off-site yet.** A fire or a theft takes the backups with the originals.
- **Databases:** Nextcloud and Immich write dumps, and those are what get backed up. The
  apps on SQLite, such as Vaultwarden and Paperless, are copied while running. At 03:30
  that is very likely fine, but it is not guaranteed.
- **Test a restore.** A backup you have never restored is a hope. On any machine:

  ```bash
  python3 -m homelab compose exec backup restic snapshots
  python3 -m homelab compose exec backup restic restore latest --target /tmp/restore
  ```
