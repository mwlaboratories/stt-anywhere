"""stt-anywhere: Speech-to-text anywhere — Wayland desktop, AR glasses, any device.

Streams audio to a Kyutai STT server via WebSocket and types words live via wtype.
Pipeline: keybind -> SIGUSR1 -> pw-record -> moshi-server (CUDA) -> wtype

Optional relay server: accepts WebSocket clients (e.g. EvenRealities G2 glasses app),
relays their audio to moshi-server, and returns transcription results as JSON.
"""

import asyncio
import json
import os
import signal
import struct
import subprocess
import sys
import time
import msgpack
import websockets

SERVER_URL = os.environ.get("STT_ANYWHERE_SERVER", "ws://127.0.0.1:8098")
API_KEY = os.environ.get("STT_ANYWHERE_API_KEY", "public_token")
AUDIO_TARGET = os.environ.get("STT_ANYWHERE_TARGET", "")  # PipeWire node id/name, empty = default
RELAY_PORT = int(os.environ.get("STT_ANYWHERE_RELAY_PORT", "0"))  # 0 = disabled
RELAY_ADDR = os.environ.get("STT_ANYWHERE_RELAY_ADDR", "0.0.0.0")

SAMPLE_RATE = 24000
CHANNELS = 1
CHUNK_SAMPLES = 1920  # 80ms at 24kHz
CHUNK_BYTES = CHUNK_SAMPLES * 4  # f32 = 4 bytes per sample
PREAMBLE_CHUNKS = SAMPLE_RATE // CHUNK_SAMPLES  # ~1s
TRAILING_CHUNKS = 3 * SAMPLE_RATE // CHUNK_SAMPLES  # ~3s


def get_default_sink():
    """Get the default PipeWire sink node name for system audio capture."""
    try:
        result = subprocess.run(
            ["wpctl", "inspect", "@DEFAULT_AUDIO_SINK@"],
            capture_output=True, text=True, check=True,
        )
        for line in result.stdout.splitlines():
            if "node.name" in line:
                return line.split("=", 1)[1].strip().strip('"')
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return None


def notify(message, urgency="normal"):
    subprocess.Popen(
        [
            "notify-send",
            "-a", "stt-anywhere",
            "-h", "string:x-canonical-private-synchronous:stt-anywhere",
            "-u", urgency,
            "stt-anywhere",
            message,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def type_text(text):
    subprocess.run(["wtype", "--", text], check=False)


async def stream_session(stop_event, capture_system_audio):
    """Run one recording+transcription session over WebSocket."""
    url = f"{SERVER_URL}/api/asr-streaming?auth_id={API_KEY}"

    pw_cmd = [
        "pw-record",
        "--raw",
        "--format=f32",
        f"--rate={SAMPLE_RATE}",
        f"--channels={CHANNELS}",
    ]

    if capture_system_audio:
        sink = get_default_sink()
        if sink:
            pw_cmd += [f"--target={sink}", "--properties=stream.capture.sink=true"]
            print(f"Capturing system audio from: {sink}", flush=True)
        else:
            print("Warning: could not find default sink, using mic", flush=True)
    elif AUDIO_TARGET:
        pw_cmd += [f"--target={AUDIO_TARGET}"]

    pw_cmd.append("-")

    recording_proc = subprocess.Popen(pw_cmd, stdout=subprocess.PIPE)

    source = "system audio" if capture_system_audio else "mic"
    notify(f"Recording ({source})...")
    print(f"Recording started ({source})...")

    words = []
    last_word_time = time.monotonic()

    try:
        async with websockets.connect(url) as ws:

            async def send_audio():
                loop = asyncio.get_event_loop()
                # Send 1s silence preamble at real-time pace (model needs this)
                silence = [0.0] * CHUNK_SAMPLES
                preamble_msg = msgpack.packb(
                    {"type": "Audio", "pcm": silence},
                    use_single_float=True,
                )
                for _ in range(SAMPLE_RATE // CHUNK_SAMPLES):
                    await ws.send(preamble_msg)
                    await asyncio.sleep(0.02)
                print("Preamble sent", flush=True)
                chunks_sent = 0
                while not stop_event.is_set():
                    try:
                        raw = await asyncio.wait_for(
                            loop.run_in_executor(
                                None, recording_proc.stdout.read, CHUNK_BYTES
                            ),
                            timeout=0.2,
                        )
                    except asyncio.TimeoutError:
                        continue
                    if not raw:
                        break
                    n_floats = len(raw) // 4
                    samples = struct.unpack(f"<{n_floats}f", raw[:n_floats * 4])
                    if chunks_sent == 0:
                        peak = max(abs(s) for s in samples) if samples else 0
                        print(f"First chunk: {n_floats} floats, peak={peak:.4f}", flush=True)
                    msg = msgpack.packb(
                        {"type": "Audio", "pcm": samples},
                        use_single_float=True,
                    )
                    await ws.send(msg)
                    chunks_sent += 1
                print(f"Sent {chunks_sent} audio chunks", flush=True)

            async def recv_words():
                nonlocal last_word_time
                msg_count = 0
                try:
                    async for message in ws:
                        data = msgpack.unpackb(message, raw=False)
                        msg_count += 1
                        if isinstance(data, dict):
                            t = data.get("type")
                            if t == "Word":
                                text = data.get("text", "")
                                print(f"WORD: '{text}'", flush=True)
                                last_word_time = time.monotonic()
                                if text:
                                    type_text(text + " ")
                                    words.append(text)
                            elif t not in ("Step", "Ready", "EndWord"):
                                print(f"MSG: {data}", flush=True)
                except websockets.exceptions.ConnectionClosed:
                    pass
                print(f"Received {msg_count} messages", flush=True)

            send_task = asyncio.create_task(send_audio())
            recv_task = asyncio.create_task(recv_words())

            await stop_event.wait()
            notify("Stopped")

            # Stop recording — closes stdout, which unblocks the read executor
            recording_proc.terminate()
            recording_proc.wait()

            # Let send_audio finish (it will see EOF from closed stdout)
            await send_task

            # Send trailing silence in a burst so the model flushes
            silence = [0.0] * CHUNK_SAMPLES
            trailing_msg = msgpack.packb(
                {"type": "Audio", "pcm": silence},
                use_single_float=True,
            )
            for _ in range(3 * SAMPLE_RATE // CHUNK_SAMPLES):
                await ws.send(trailing_msg)

            # Wait for final words: stop when no Word received for 0.5s, max 2s
            last_word_time = time.monotonic()
            drain_start = time.monotonic()
            while time.monotonic() - drain_start < 2.0:
                if time.monotonic() - last_word_time > 0.5:
                    break
                await asyncio.sleep(0.05)

            # Close WebSocket, then cancel the receiver
            await ws.close()
            recv_task.cancel()
            try:
                await recv_task
            except asyncio.CancelledError:
                pass

    finally:
        if recording_proc.poll() is None:
            recording_proc.terminate()
            recording_proc.wait()

    full_text = " ".join(words).strip()
    if full_text:
        print(f"Transcribed: {full_text}")
    else:
        print("No speech detected.")
        notify("No speech detected", urgency="low")


async def relay_client(client_ws):
    """Handle one relay client: bridge between browser WebSocket and moshi-server."""
    moshi_url = f"{SERVER_URL}/api/asr-streaming?auth_id={API_KEY}"
    print(f"[relay] Client connected, relaying to {SERVER_URL}", flush=True)

    moshi_ws = None
    try:
        moshi_ws = await websockets.connect(moshi_url)

        # Send preamble silence to moshi
        silence_msg = msgpack.packb(
            {"type": "Audio", "pcm": [0.0] * CHUNK_SAMPLES},
            use_single_float=True,
        )
        for _ in range(PREAMBLE_CHUNKS):
            await moshi_ws.send(silence_msg)
        print("[relay] Preamble sent", flush=True)

        await client_ws.send(json.dumps({"type": "ready"}))

        async def forward_audio():
            """Client binary f32 PCM -> msgpack -> moshi."""
            chunks = 0
            try:
                async for message in client_ws:
                    if isinstance(message, bytes):
                        n_floats = len(message) // 4
                        if n_floats == 0:
                            continue
                        if chunks == 0:
                            samples = struct.unpack(f"<{n_floats}f", message[:n_floats * 4])
                            peak = max(abs(s) for s in samples) if samples else 0
                            print(f"[relay] First audio chunk: {len(message)} bytes, {n_floats} floats, peak={peak:.4f}", flush=True)
                        else:
                            samples = struct.unpack(f"<{n_floats}f", message[:n_floats * 4])
                        msg = msgpack.packb(
                            {"type": "Audio", "pcm": samples},
                            use_single_float=True,
                        )
                        await moshi_ws.send(msg)
                        chunks += 1
                    else:
                        print(f"[relay] Non-binary message: type={type(message).__name__} len={len(message)} preview={str(message)[:200]}", flush=True)
            except websockets.exceptions.ConnectionClosed:
                pass
            print(f"[relay] Forward audio done: {chunks} chunks sent", flush=True)

        async def forward_words():
            """Moshi msgpack responses -> JSON -> client."""
            try:
                async for message in moshi_ws:
                    data = msgpack.unpackb(message, raw=False)
                    if isinstance(data, dict):
                        try:
                            await client_ws.send(json.dumps(data))
                        except websockets.exceptions.ConnectionClosed:
                            break
            except websockets.exceptions.ConnectionClosed:
                pass

        audio_task = asyncio.create_task(forward_audio())
        words_task = asyncio.create_task(forward_words())

        # Wait for client to disconnect (audio_task finishes)
        await audio_task

        # Send trailing silence burst so moshi flushes (best-effort)
        try:
            for _ in range(TRAILING_CHUNKS):
                await moshi_ws.send(silence_msg)
        except websockets.exceptions.ConnectionClosed:
            pass

        # Drain: wait for final words (max 2s, stop if silent for 0.5s)
        drain_start = time.monotonic()
        while time.monotonic() - drain_start < 2.0:
            await asyncio.sleep(0.05)

        words_task.cancel()
        try:
            await words_task
        except asyncio.CancelledError:
            pass

    except Exception as e:
        print(f"[relay] Error: {e}", flush=True)
        try:
            await client_ws.send(json.dumps({"type": "error", "text": str(e)}))
        except Exception:
            pass
    finally:
        # Always close moshi WebSocket to free the server slot
        if moshi_ws is not None:
            try:
                await moshi_ws.close()
            except Exception:
                pass
        print("[relay] Client disconnected, moshi connection closed", flush=True)


async def run_relay_server():
    """Start the WebSocket relay server for remote STT clients."""
    print(f"[relay] Listening on ws://{RELAY_ADDR}:{RELAY_PORT}", flush=True)

    async with websockets.serve(relay_client, RELAY_ADDR, RELAY_PORT):
        await asyncio.Future()  # run forever


async def main():
    loop = asyncio.get_event_loop()

    capture_system_audio = False

    def toggle_source():
        nonlocal capture_system_audio
        capture_system_audio = not capture_system_audio
        mode = "system audio" if capture_system_audio else "mic"
        print(f"Audio source: {mode}", flush=True)
        notify(f"Source: {mode}")

    loop.add_signal_handler(signal.SIGUSR2, toggle_source)

    # Start relay server if configured
    if RELAY_PORT:
        asyncio.create_task(run_relay_server())

    notify("Ready (mic)")
    relay_info = f" | Relay: ws://{RELAY_ADDR}:{RELAY_PORT}" if RELAY_PORT else ""
    print(f"Ready. Server: {SERVER_URL}{relay_info}")
    print("SIGUSR1: toggle recording | SIGUSR2: toggle mic/system audio")

    toggled = asyncio.Event()
    loop.add_signal_handler(signal.SIGUSR1, toggled.set)

    while True:
        toggled.clear()
        await toggled.wait()

        # Use a fresh event for stopping this session
        stop_event = asyncio.Event()
        loop.remove_signal_handler(signal.SIGUSR1)
        loop.add_signal_handler(signal.SIGUSR1, stop_event.set)

        try:
            await stream_session(stop_event, capture_system_audio)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            notify(f"Error: {e}", urgency="critical")
        finally:
            loop.remove_signal_handler(signal.SIGUSR1)
            loop.add_signal_handler(signal.SIGUSR1, toggled.set)


if __name__ == "__main__":
    asyncio.run(main())
