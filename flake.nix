{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  nixConfig = {
    extra-substituters = [ "https://cuda-maintainers.cachix.org" ];
    extra-trusted-public-keys = [ "cuda-maintainers.cachix.org-1:0dq3bujKpuEPMCX6U4WylrUDZ9JyUG0VpVZa7CNfq5E=" ];
  };

  outputs =
    { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
      cudaPkgs = import nixpkgs {
        inherit system;
        config = {
          allowUnfree = true;
          cudaSupport = true;
        };
      };
      python = pkgs.python3.withPackages (ps: [
        ps.websockets
        ps.msgpack
      ]);
    in
    {
      packages.${system} = {
        default = pkgs.callPackage ./nix/package.nix {
          inherit python;
          src = self;
        };

        moshi-server = (cudaPkgs.moshi.override { cudaCapability = "8.6"; }).overrideAttrs (old: {
          buildInputs = old.buildInputs ++ [ pkgs.oniguruma ];
          env = (old.env or { }) // {
            RUSTONIG_SYSTEM_LIBONIG = "1";
          };
          meta = old.meta // { mainProgram = "moshi-server"; };
        });
      };

      homeManagerModules.default = ./nix/hm-module.nix;

      nixosModules.default = ./nix/nixos-module.nix;

      devShells.${system}.default = pkgs.mkShell {
        packages = [
          python
          pkgs.wtype
          pkgs.libnotify
          pkgs.pipewire
        ];
      };
    };
}
