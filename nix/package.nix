{
  pkgs,
  python,
  src,
}:
pkgs.writeShellApplication {
  name = "stt-anywhere";
  runtimeInputs = [
    python
    pkgs.wtype
    pkgs.libnotify
    pkgs.pipewire
    pkgs.wireplumber
  ];
  text = ''
    exec python ${src}/stt-anywhere.py "$@"
  '';
}
