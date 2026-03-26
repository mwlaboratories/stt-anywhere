{
  config,
  lib,
  ...
}:
let
  cfg = config.programs.stt-anywhere;
in
{
  options.programs.stt-anywhere = {
    enable = lib.mkEnableOption "stt-anywhere push-to-talk speech-to-text";

    package = lib.mkOption {
      type = lib.types.package;
      description = "The stt-anywhere package to use.";
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
