{ ... }:
{
  imports = [
    ./system-units.nix
    ./user-units.nix
    ./etc-host.nix
  ];

  nixpkgs.hostPlatform = "x86_64-linux";
}
