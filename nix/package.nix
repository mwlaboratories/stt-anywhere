{
  pkgs,
  python,
  src,
}:
pkgs.writeShellApplication {
  name = "wl-whispr";
  runtimeInputs = [
    python
    pkgs.wtype
    pkgs.libnotify
    pkgs.pipewire
    pkgs.wireplumber
  ];
  text = ''
    exec python ${src}/wl-whispr.py "$@"
  '';
}
