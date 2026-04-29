{ repoRoot, ... }:
{
  # /etc/sleet1213 — agent runtime config. nick-groups.json identifies
  # which Twitch nicks get which agent permissions; the policies/ dir
  # holds the OPA/rego rules + JSON config that gate skill filesystem
  # access from inside mcp-v8.
  environment.etc = {
    "sleet1213/nick-groups.json".source =
      "${repoRoot}/etc/sleet1213/nick-groups.json";
    "sleet1213/policies/skill-filesystem.rego".source =
      "${repoRoot}/etc/sleet1213/policies/skill-filesystem.rego";
    "sleet1213/policies/skills-fs.json".source =
      "${repoRoot}/etc/sleet1213/policies/skills-fs.json";
  };
}
