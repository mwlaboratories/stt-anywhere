# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

wl-whispr is a local push-to-talk streaming speech-to-text daemon for Wayland. It streams audio to a Kyutai STT server (moshi-server) via WebSocket and types words live as they're recognized. Works on any Wayland compositor (niri, Hyprland, Sway, COSMIC, etc.).

**Pipeline:** keybind -> SIGUSR1 -> `pw-record` -> moshi-server (CUDA) -> `wtype` (live)

## Architecture

Two-service design: a Rust STT server (`moshi-server`) handles GPU inference, and a lightweight Python client (`wl-whispr.py`) manages recording and text injection.

```
[moshi-server]                        [wl-whispr.py]
(systemd service)                     (systemd service)
Loads STT model in VRAM               Lightweight WS client
Listens ws://localhost:8098
                                      SIGUSR1 ->
/api/asr-streaming <--- WebSocket --- pw-record (24kHz f32 mono)
    |                                         |
    +-- Word tokens --- WebSocket --> wtype (types each word live)
                                      SIGUSR1 -> stop
                                      SIGUSR2 -> toggle mic/system audio
```

- **STT Server:** `moshi-server` (Rust, candle CUDA) runs as a separate systemd service, keeps model in VRAM
- **Recording:** PipeWire (`pw-record --raw`) captures audio as f32 at 24kHz mono, streamed to stdout
- **Streaming:** WebSocket connection to moshi-server, audio sent as MessagePack chunks (1920 samples = 80ms)
- **Text injection:** `wtype` types each word as it arrives from the server (~500ms latency)
- **Notifications:** `notify-send` for status updates (recording, transcription results, errors)
- **Signal handling:** SIGUSR1 toggles recording, SIGUSR2 toggles mic/system audio capture

## File Structure

```
flake.nix              # Flake: packages, homeManagerModules, nixosModules, devShells
wl-whispr.py           # Main Python application (WebSocket client)
nix/
  package.nix          # Package derivation (writeShellApplication wrapper)
  hm-module.nix        # Home Manager module (services.wl-whispr + moshi-server)
  nixos-module.nix     # NixOS module (programs.wl-whispr)
```

## Build & Run

Nix flake, x86_64-linux only. The wl-whispr client itself does not require CUDA — that's handled by moshi-server:

```sh
nix build                  # builds the wl-whispr client wrapper
nix build .#moshi-server   # builds moshi-server with CUDA (from nixpkgs moshi + oniguruma fix)
nix run                    # runs client (needs moshi-server running separately)
nix develop                # dev shell with all dependencies
```

The `moshi-server` package in the flake overrides `pkgs.moshi` from nixpkgs to fix the `onig_sys` build (`RUSTONIG_SYSTEM_LIBONIG=1`) and sets `cudaCapability` for the target GPU. Adjust the capability in `flake.nix` to match your GPU (e.g., `"8.6"` for RTX 3090, `"8.9"` for RTX 4090).

## Critical: pw-record --raw flag

`pw-record` outputs a 24-byte AU-style header when writing to stdout. Bytes 8-11 are `0xffffffff` (unknown size) which parses as `NaN` when interpreted as f32, corrupting the moshi model state. The `--raw` flag skips this header — never remove it.

## Usage as a Module

```nix
# flake.nix inputs:
inputs.wl-whispr.url = "github:user/wl-whispr";

# Home Manager (recommended — runs both services):
imports = [ inputs.wl-whispr.homeManagerModules.default ];
services.wl-whispr = {
  enable = true;
  package = inputs.wl-whispr.packages.x86_64-linux.default;
  moshiPackage = inputs.wl-whispr.packages.x86_64-linux.moshi-server;
  language = "en";
};

# NixOS (just adds packages to systemPackages):
imports = [ inputs.wl-whispr.nixosModules.default ];
programs.wl-whispr = {
  enable = true;
  package = inputs.wl-whispr.packages.x86_64-linux.default;
  moshiPackage = inputs.wl-whispr.packages.x86_64-linux.moshi-server;
};
```

Keybind examples (any Wayland compositor):
```kdl
// niri
binds {
    Mod+V { spawn "systemctl" "--user" "kill" "-s" "USR1" "wl-whispr.service"; }
    Mod+Shift+V { spawn "systemctl" "--user" "kill" "-s" "USR2" "wl-whispr.service"; }
}
```

## Home Manager Module Options

Under `services.wl-whispr`:
- `enable` — enable both systemd user services (moshi-server + wl-whispr)
- `package` — the wl-whispr package
- `moshiPackage` — the moshi-server package (user-provided, must include CUDA support)
- `sttModel` — HuggingFace model repo (default: `"kyutai/stt-1b-en_fr-candle"`)
- `language` — language hint, null for auto-detect (default: `null`)
- `serverPort` — port for moshi-server (default: `8098`)
- `extraEnvironment` — extra environment variables (default: `{}`)

## Environment Variables

- `WL_WHISPR_SERVER` — WebSocket URL for moshi-server (default: `ws://127.0.0.1:8098`)
- `WL_WHISPR_API_KEY` — API key for moshi-server auth (default: `public_token`)
- `WL_WHISPR_TARGET` — PipeWire node id/name for audio capture (default: empty = default mic)

## Runtime Dependencies

- **wl-whispr.py:** Python with `websockets` + `msgpack`, `wtype`, `libnotify`, `pipewire`, `wireplumber`
- **moshi-server:** Rust binary with CUDA (candle), provided by user

Models are downloaded by moshi-server from HuggingFace on first run (~2.4GB for the 1B model).
