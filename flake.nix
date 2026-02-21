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

      homeManagerModules.default =
        { config, lib, pkgs, ... }:
        {
          imports = [ ./nix/hm-module.nix ];

          config = lib.mkIf config.services.wayland-stt.enable {
            services.wayland-stt = {
              package = lib.mkDefault (pkgs.callPackage ./nix/package.nix {
                python = pkgs.python3.withPackages (ps: [
                  ps.websockets
                  ps.msgpack
                ]);
                src = self;
              });
              moshiPackage = lib.mkDefault (
                let
                  cudaPkgs' = import nixpkgs {
                    inherit (pkgs.stdenv.hostPlatform) system;
                    config = {
                      allowUnfree = true;
                      cudaSupport = true;
                    };
                  };
                in
                (cudaPkgs'.moshi.override {
                  inherit (config.services.wayland-stt) cudaCapability;
                }).overrideAttrs (old: {
                  buildInputs = old.buildInputs ++ [ pkgs.oniguruma ];
                  env = (old.env or { }) // {
                    RUSTONIG_SYSTEM_LIBONIG = "1";
                  };
                  meta = old.meta // { mainProgram = "moshi-server"; };
                })
              );
            };
          };
        };

      nixosModules.default =
        { config, lib, pkgs, ... }:
        {
          imports = [ ./nix/nixos-module.nix ];

          config = lib.mkIf config.programs.wayland-stt.enable {
            programs.wayland-stt = {
              package = lib.mkDefault (pkgs.callPackage ./nix/package.nix {
                python = pkgs.python3.withPackages (ps: [
                  ps.websockets
                  ps.msgpack
                ]);
                src = self;
              });
              moshiPackage = lib.mkDefault self.packages.${pkgs.stdenv.hostPlatform.system}.moshi-server;
            };
          };
        };

      devShells.${system}.default = pkgs.mkShell {
        packages = [
          python
          pkgs.wtype
          pkgs.libnotify
          pkgs.pipewire
          pkgs.wireplumber
        ];
      };
    };
}
