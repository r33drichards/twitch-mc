{ repoRoot, ... }:
let
  unit = name: {
    "systemd/system/${name}.service".source =
      "${repoRoot}/units/system/${name}.service";
  };
in
{
  # System-level systemd units. Verbatim from /etc/systemd/system/<name>.service
  # on the host; system-manager places them at the same path.
  #
  # Each unit references state/binaries on the host (e.g. /var/lib/btone,
  # /usr/local/bin/...) — those locations are NOT managed here, just the
  # unit declarations. Env files for secrets (/etc/btone-bot.env,
  # /etc/btone-stream/env, /etc/litellm/config.yaml) stay manually-managed.
  environment.etc =
    unit "btone-audio"
    // unit "btone-bot"
    // unit "btone-stream"
    // unit "pulse-game"
    // unit "redis"
    // unit "xorg-headless";
}
