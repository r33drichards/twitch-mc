{ repoRoot, ... }:
{
  environment.etc = {
    # /etc/sleet1213 — agent runtime config. nick-groups.json identifies
    # which Twitch nicks get which agent permissions; the policies/ dir
    # holds the OPA/rego rules + JSON config that gate skill filesystem
    # access from inside mcp-v8.
    "sleet1213/nick-groups.json".source =
      "${repoRoot}/etc/sleet1213/nick-groups.json";
    "sleet1213/policies/skill-filesystem.rego".source =
      "${repoRoot}/etc/sleet1213/policies/skill-filesystem.rego";
    "sleet1213/policies/skills-fs.json".source =
      "${repoRoot}/etc/sleet1213/policies/skills-fs.json";

    # /etc/X11/xorg-headless.conf — Xorg :99 config for the headless
    # NVIDIA-rendered display the bot+stream use. ServerFlags now
    # disables BlankTime/StandbyTime/SuspendTime/OffTime — without
    # this the screen saver kicks in after 10 min, blanks the drawable,
    # and MC's swap-buffers can't recover, leading to a black stream.
    "X11/xorg-headless.conf".source =
      "${repoRoot}/etc/X11/xorg-headless.conf";
  };
}
