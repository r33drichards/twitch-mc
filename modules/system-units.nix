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
    // unit "xorg-headless";
  # pulse-game.service intentionally dropped — it ran a separate
  # user-mode pulseaudio at /tmp/pulse-game-audio that broke on D-Bus
  # autolaunch (no $DISPLAY). The game_audio sink is now loaded by
  # btone-audio's system pulse instance and btone-stream connects to
  # /var/run/pulse/native instead.
  # Note: redis.service is intentionally NOT managed here. The host runs
  # redis-server.service from /usr/lib/systemd/system/, installed by the
  # Ubuntu redis-server package. Managing a duplicate redis.service alias
  # via system-manager would conflict with apt's unit.
}
