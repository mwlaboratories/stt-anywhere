"""wayland-stt: Local push-to-talk streaming speech-to-text for Wayland.

Streams audio to a Kyutai STT server via WebSocket and types words live via wtype.
Pipeline: keybind -> SIGUSR1 -> pw-record -> moshi-server (CUDA) -> wtype
"""

import asyncio
import os
import signal
import struct
import subprocess
import sys
import time

import msgpack
import websockets

SERVER_URL = os.environ.get("WAYLAND_STT_SERVER", "ws://127.0.0.1:8098")
API_KEY = os.environ.get("WAYLAND_STT_API_KEY", "public_token")
AUDIO_TARGET = os.environ.get("WAYLAND_STT_TARGET", "")  # PipeWire node id/name, empty = default

SAMPLE_RATE = 24000
CHANNELS = 1
CHUNK_SAMPLES = 1920  # 80ms at 24kHz
CHUNK_BYTES = CHUNK_SAMPLES * 4  # f32 = 4 bytes per sample


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
            "-a", "wayland-stt",
            "-h", "string:x-canonical-private-synchronous:wayland-stt",
            "-u", urgency,
            "wayland-stt",
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
                                    if words:
                                        type_text(" " + text)
                                    else:
                                        type_text(text)
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

    notify("Ready (mic)")
    print(f"Ready. Server: {SERVER_URL}")
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
