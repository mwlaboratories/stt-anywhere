{
  config,
  lib,
  ...
}:
let
  cfg = config.programs.wayland-stt;
in
{
  options.programs.wayland-stt = {
    enable = lib.mkEnableOption "wayland-stt push-to-talk speech-to-text";

    package = lib.mkOption {
      type = lib.types.package;
      description = "The wayland-stt package to use.";
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
