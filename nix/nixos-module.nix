{
  config,
  lib,
  ...
}:
let
  cfg = config.programs.wl-whispr;
in
{
  options.programs.wl-whispr = {
    enable = lib.mkEnableOption "wl-whispr push-to-talk speech-to-text";

    package = lib.mkOption {
      type = lib.types.package;
      description = "The wl-whispr package to use.";
    };

    moshiPackage = lib.mkOption {
      type = lib.types.package;
      description = "The moshi-server package to use.";
    };
  };

  config = lib.mkIf cfg.enable {
    environment.systemPackages = [
      cfg.package
      cfg.moshiPackage
    ];
  };
}
