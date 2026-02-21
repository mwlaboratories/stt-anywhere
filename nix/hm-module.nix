{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.services.wayland-stt;

  sttConfig = pkgs.writeText "stt-config.toml" ''
    static_dir = "/tmp"
    log_dir = "/tmp"
    instance_name = "wayland-stt-moshi"
    authorized_ids = ["public_token"]

    [modules.asr]
    path = "/api/asr-streaming"
    type = "BatchedAsr"
    lm_model_file = "hf://${cfg.sttModel}/model.safetensors"
    text_tokenizer_file = "hf://${cfg.sttModel}/tokenizer_en_fr_audio_8000.model"
    audio_tokenizer_file = "hf://${cfg.sttModel}/mimi-pytorch-e351c8d8@125.safetensors"
    asr_delay_in_tokens = 6
    batch_size = 64
    conditioning_learnt_padding = true
    temperature = 0.0

    [modules.asr.model]
    audio_vocab_size = 2049
    text_in_vocab_size = 8001
    text_out_vocab_size = 8000
    audio_codebooks = 32

    [modules.asr.model.transformer]
    d_model = 2048
    num_heads = 16
    num_layers = 16
    dim_feedforward = 8192
    causal = true
    norm_first = true
    bias_ff = false
    bias_attn = false
    context = 750
    max_period = 100000
    use_conv_block = false
    use_conv_bias = true
    gating = "silu"
    norm = "RmsNorm"
    positional_embedding = "Rope"
    conv_layout = false
    conv_kernel_size = 3
    kv_repeat = 1
    max_seq_len = 40960

    [modules.asr.model.extra_heads]
    num_heads = 4
    dim = 6
  '';
in
{
  options.services.wayland-stt = {
    enable = lib.mkEnableOption "wayland-stt push-to-talk speech-to-text daemon";

    package = lib.mkOption {
      type = lib.types.package;
      description = "The wayland-stt package to use.";
    };

    moshiPackage = lib.mkOption {
      type = lib.types.package;
      description = "The moshi-server package to use.";
    };

    cudaCapability = lib.mkOption {
      type = lib.types.str;
      default = "8.6";
      description = "CUDA compute capability for moshi-server (e.g. \"8.6\" for RTX 3090, \"8.9\" for RTX 4090).";
    };

    sttModel = lib.mkOption {
      type = lib.types.str;
      default = "kyutai/stt-1b-en_fr-candle";
      description = "HuggingFace model repo for Kyutai STT.";
    };

    serverPort = lib.mkOption {
      type = lib.types.port;
      default = 8098;
      description = "Port for the moshi-server to listen on.";
    };

    extraEnvironment = lib.mkOption {
      type = lib.types.attrsOf lib.types.str;
      default = { };
      description = "Extra environment variables to pass to the wayland-stt service.";
    };
  };

  config = lib.mkIf cfg.enable {
    systemd.user.services.moshi-server = {
      Unit = {
        Description = "Kyutai STT server for wayland-stt";
        After = [ "graphical-session.target" ];
        PartOf = [ "graphical-session.target" ];
      };

      Service = {
        Type = "simple";
        ExecStart = "${lib.getExe' cfg.moshiPackage "moshi-server"} worker --config ${sttConfig} --port ${toString cfg.serverPort} --addr 127.0.0.1";
        Restart = "on-failure";
        RestartSec = 5;
      };

      Install = {
        WantedBy = [ "graphical-session.target" ];
      };
    };

    systemd.user.services.wayland-stt = {
      Unit = {
        Description = "wayland-stt push-to-talk speech-to-text daemon";
        After = [
          "graphical-session.target"
          "moshi-server.service"
        ];
        PartOf = [ "graphical-session.target" ];
        Requires = [ "moshi-server.service" ];
      };

      Service = {
        Type = "simple";
        ExecStart = "${cfg.package}/bin/wayland-stt";
        Restart = "on-failure";
        RestartSec = 5;
        Environment =
          [
            "WAYLAND_STT_SERVER=ws://127.0.0.1:${toString cfg.serverPort}"
          ]
          ++ lib.mapAttrsToList (name: value: "${name}=${value}") cfg.extraEnvironment;
      };

      Install = {
        WantedBy = [ "graphical-session.target" ];
      };
    };
  };
}
