{ ... }:
{
  imports = [
    ./system-units.nix
    ./user-units.nix
    ./etc-sleet1213.nix
  ];

  nixpkgs.hostPlatform = "x86_64-linux";
}
