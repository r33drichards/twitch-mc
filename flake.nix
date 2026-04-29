{
  description = "twitch-mc — sleet1213 Twitch agent + btone Minecraft bot infra (system-manager)";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";

    system-manager = {
      url = "github:numtide/system-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # Source-only inputs (no flake.nix in the upstream repos). Their
    # content is referenced from systemd units / wrapper scripts so that
    # version pinning happens via flake.lock.
    mcp-js = {
      url = "github:r33drichards/mcp-js";
      flake = false;
    };

    minecraft-data = {
      url = "github:PrismarineJS/minecraft-data";
      flake = false;
    };
  };

  outputs = { self, nixpkgs, system-manager, mcp-js, minecraft-data }: {
    systemConfigs.default = system-manager.lib.makeSystemConfig {
      modules = [ ./modules ];
      extraSpecialArgs = {
        inherit mcp-js minecraft-data;
        repoRoot = self;
      };
    };
  };
}
