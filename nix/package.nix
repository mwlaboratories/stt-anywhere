{
  pkgs,
  python,
  src,
}:
pkgs.writeShellApplication {
  name = "wayland-stt";
  runtimeInputs = [
    python
    pkgs.wtype
    pkgs.libnotify
    pkgs.pipewire
    pkgs.wireplumber
  ];
  text = ''
    exec python ${src}/wayland-stt.py "$@"
  '';
}
