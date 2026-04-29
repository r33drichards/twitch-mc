{ repoRoot, ... }:
let
  unit = name: {
    "systemd/user/${name}.service".source =
      "${repoRoot}/units/user/${name}.service";
  };
in
{
  # System-wide user systemd units. system-manager writes these to
  # /etc/systemd/user/, where any user's `systemctl --user` will find
  # them. The currently-active copies under ~ubuntu/.config/systemd/user/
  # shadow these and must be removed before the system-wide copies take
  # effect — see the migration steps in README.md.
  environment.etc =
    unit "sleet1213-hud-poller"
    // unit "sleet1213-irc"
    // unit "sleet1213-mc-bridge"
    // unit "sleet1213-temporal"
    // unit "sleet1213-webhook"
    // unit "sleet1213-worker";
}
