# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

stt-anywhere is a local push-to-talk streaming speech-to-text daemon for Wayland. It streams audio to a Kyutai STT server (moshi-server) via WebSocket and types words live as they're recognized. Works on any Wayland compositor (niri, Hyprland, Sway, COSMIC, etc.).

**Pipeline:** keybind -> SIGUSR1 -> `pw-record` -> moshi-server (CUDA) -> `wtype` (live)

## Architecture

Two-service design: a Rust STT server (`moshi-server`) handles GPU inference, and a lightweight Python client (`stt-anywhere.py`) manages recording and text injection.

```
[moshi-server]                        [stt-anywhere.py]
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
- **Notifications:** `notify-send` (fire-and-forget via `Popen`) for start/stop/error only — no transcription text shown
- **Signal handling:** SIGUSR1 toggles recording, SIGUSR2 toggles mic/system audio capture
- **Stop path:** Immediate "Stopped" notification, trailing silence sent in burst (not real-time), smart drain waits for server to finish (0.5s idle or 2s max)
- **Preamble:** 1s silence sent at accelerated pace (~0.25s wall time) for model warmup

## Deployment (path: flake input)

Changes to `stt-anywhere.py` must be **staged or committed** before rebuilding — Nix `path:` flake inputs ignore unstaged files.

```sh
# In stt-anywhere repo:
git add stt-anywhere.py

# In nixos-config repo:
nix flake update stt-anywhere
sudo nixos-rebuild switch
systemctl --user restart stt-anywhere
```

## File Structure

```
flake.nix              # Flake: packages, homeManagerModules, nixosModules, devShells
stt-anywhere.py           # Main Python application (WebSocket client)
nix/
  package.nix          # Package derivation (writeShellApplication wrapper)
  hm-module.nix        # Home Manager module (services.stt-anywhere + moshi-server)
  nixos-module.nix     # NixOS module (programs.stt-anywhere)
```

## Build & Run

Nix flake, x86_64-linux only. The stt-anywhere client itself does not require CUDA — that's handled by moshi-server:

```sh
nix build                  # builds the stt-anywhere client wrapper
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
inputs.stt-anywhere.url = "github:user/stt-anywhere";

# Home Manager — local server + client (default):
imports = [ inputs.stt-anywhere.homeManagerModules.default ];
services.stt-anywhere = {
  enable = true;
  cudaCapability = "8.6";  # "8.9" for RTX 4090, etc.
  serverAddr = "0.0.0.0";  # optional: accept remote connections (e.g. Tailscale)
};

# Home Manager — remote client only (no local GPU):
imports = [ inputs.stt-anywhere.homeManagerModules.default ];
services.stt-anywhere = {
  enable = true;
  enableServer = false;          # no local moshi-server
  serverUrl = "ws://eos:8098";   # connect to remote server over Tailscale
};

# NixOS (just adds packages to systemPackages):
imports = [ inputs.stt-anywhere.nixosModules.default ];
programs.stt-anywhere.enable = true;
```

Keybind examples (any Wayland compositor):
```kdl
// niri
binds {
    Mod+V { spawn "systemctl" "--user" "kill" "-s" "USR1" "stt-anywhere.service"; }
    Mod+Shift+V { spawn "systemctl" "--user" "kill" "-s" "USR2" "stt-anywhere.service"; }
}
```

## Home Manager Module Options

Under `services.stt-anywhere`:
- `enable` — enable the stt-anywhere client service (and moshi-server if `enableServer` is true)
- `enableServer` — whether to run a local moshi-server (default: `true`). Set to `false` on machines without a GPU that connect to a remote server
- `cudaCapability` — CUDA compute capability for moshi-server (default: `"8.6"`, e.g. `"8.9"` for RTX 4090)
- `serverAddr` — address for moshi-server to bind to (default: `"127.0.0.1"`). Set to `"0.0.0.0"` to accept remote connections (e.g. over Tailscale)
- `serverUrl` — WebSocket URL the client connects to (default: `"ws://127.0.0.1:${serverPort}"`). Override to point at a remote server (e.g. `"ws://eos:8098"`)
- `serverPort` — port for moshi-server (default: `8098`)
- `package` — the stt-anywhere package (auto-provided, override for custom builds)
- `moshiPackage` — the moshi-server package (auto-built from `cudaCapability`, override for custom builds)
- `sttModel` — HuggingFace model repo (default: `"kyutai/stt-1b-en_fr-candle"`)
- `extraEnvironment` — extra environment variables (default: `{}`)

## Environment Variables

- `STT_ANYWHERE_SERVER` — WebSocket URL for moshi-server (default: `ws://127.0.0.1:8098`)
- `STT_ANYWHERE_API_KEY` — API key for moshi-server auth (default: `public_token`)
- `STT_ANYWHERE_TARGET` — PipeWire node id/name for audio capture (default: empty = default mic)

## Runtime Dependencies

- **stt-anywhere.py:** Python with `websockets` + `msgpack`, `wtype`, `libnotify`, `pipewire`, `wireplumber`
- **moshi-server:** Rust binary with CUDA (candle), provided by user

Models are downloaded by moshi-server from HuggingFace on first run (~2.4GB for the 1B model).
