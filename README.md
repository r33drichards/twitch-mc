# twitch-mc

Sleet1213 Twitch chat agent + btone Minecraft bot infrastructure, packaged
declaratively with [system-manager](https://github.com/numtide/system-manager)
for non-NixOS Ubuntu hosts.

## Layout

```
flake.nix                         # nixpkgs + system-manager + mcp-js + minecraft-data inputs
modules/
  default.nix                     # imports the three modules below
  system-units.nix                # 6 /etc/systemd/system/ units (btone-*, pulse-game, redis, xorg-headless)
  user-units.nix                  # 5 /etc/systemd/user/ units (sleet1213-*)
  etc-sleet1213.nix               # /etc/sleet1213/{nick-groups.json,policies/*}
units/
  system/                         # raw .service files, vendored verbatim
  user/
etc/sleet1213/                    # config files vendored verbatim
apps/
  sleet1213/                      # subtree of r33drichards/sleet1213
  mca/                            # subtree of r33drichards/mca
Makefile                          # build / activate / deploy convenience
```

## Activate on the EC2 host

First-time (BOOTSTRAP — read before running). system-manager refuses to
clobber files at `/etc/...` that aren't already under its management, so
the existing unit files have to step aside before the first `switch`.
Move them somewhere safe rather than deleting — recovery is fast if
something goes wrong:

```bash
ssh ubuntu@<host>
git clone https://github.com/r33drichards/twitch-mc.git ~/twitch-mc

# Park existing files out of the way
sudo mkdir -p /var/backups/pre-twitch-mc/{system,user,sleet1213}
for u in btone-audio btone-bot btone-stream pulse-game redis xorg-headless; do
  sudo mv /etc/systemd/system/$u.service /var/backups/pre-twitch-mc/system/
done
sudo cp -a /etc/sleet1213 /var/backups/pre-twitch-mc/sleet1213/
sudo rm /etc/sleet1213/nick-groups.json /etc/sleet1213/policies/*

# First activation — services don't restart because unit text is identical
cd ~/twitch-mc
nix run github:numtide/system-manager -- switch --flake .#default --sudo

# Sanity check: the same units now exist at the same paths
for u in btone-audio btone-bot btone-stream pulse-game redis xorg-headless; do
  systemctl status $u --no-pager | head -3
done
```

For the user units, see "Migrating the user units" below — they're a
separate one-shot.

Subsequent updates:

```bash
ssh ubuntu@<host> 'cd ~/twitch-mc && git pull && \
  nix run github:numtide/system-manager -- switch --flake .#default --sudo'
```

(or `make deploy` from a clone with `HOST=ubuntu@…` set).

## Migrating the user units (one-time)

The 5 `sleet1213-*` units currently live at
`~ubuntu/.config/systemd/user/*.service` and shadow the system-wide
copies that system-manager places at `/etc/systemd/user/`. After the
first activation, drop the shadows so the managed copies take effect:

```bash
systemctl --user stop sleet1213-irc sleet1213-worker sleet1213-webhook \
  sleet1213-temporal sleet1213-hud-poller
rm ~/.config/systemd/user/sleet1213-*.service
systemctl --user daemon-reload
systemctl --user enable --now sleet1213-temporal sleet1213-webhook \
  sleet1213-worker sleet1213-irc sleet1213-hud-poller
```

## Out of scope (intentionally manual)

- **State directories**: `/var/lib/btone/`, `/home/ubuntu/sleet1213/.temporal-db`,
  `~/.openclaw/{workspace,delivery-queue}`, `btone-mc-work/`. World saves,
  databases, queues — never in the repo.
- **Secret env files**: `/etc/btone-bot.env`, `/etc/btone-stream/env`,
  `/etc/litellm/config.yaml`, `/home/ubuntu/sleet1213/.env`. Plan: sops-nix
  follow-up.
- **OpenClaw**: `openclaw.service`, `openclaw-gateway.service`,
  `litellm.service`, `event-bridge.service` are managed elsewhere.
- **Source code paths**: services still reference
  `/home/ubuntu/{sleet1213,mca-src}/...` — relocating into the repo
  checkout is a separate phase to avoid stream-time downtime.

## Apps

`apps/sleet1213/` and `apps/mca/` are git-subtree merges of the upstream
repos with full history preserved. To pull in upstream changes:

```bash
git subtree pull --prefix=apps/sleet1213 \
  https://github.com/r33drichards/sleet1213.git master
```

To push changes back upstream:

```bash
git subtree push --prefix=apps/sleet1213 \
  https://github.com/r33drichards/sleet1213.git master
```
