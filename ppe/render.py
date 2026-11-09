"""Render the kept parts of a video into a new MP4.

Cuts are snapped to whole video frames. The video is cut with FFmpeg's `select`
filter (by frame number) and the audio is assembled in Python with the exact
same boundaries plus tiny fades, so A/V stay in sync and cuts don't click.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import wave

import numpy as np

from .ffmpeg import MediaInfo, decode_audio, ffmpeg_exe

AUDIO_RATE = 48000
FADE = 0.004  # seconds of fade at each cut


def snap_to_frames(intervals, fps: float) -> list[tuple[int, int]]:
    frames: list[tuple[int, int]] = []
    for s, e in intervals:
        a, b = int(round(s * fps)), int(round(e * fps))
        if b <= a:
            continue
        if frames and a <= frames[-1][1]:
            frames[-1] = (frames[-1][0], max(b, frames[-1][1]))
        else:
            frames.append((a, b))
    return frames


def _write_audio(src: str, frames, fps: float, out_wav: str) -> None:
    audio = decode_audio(src, AUDIO_RATE, channels=2)
    fade_n = int(FADE * AUDIO_RATE)
    ramp = np.linspace(0.0, 1.0, fade_n, dtype=np.float32)[:, None]
    with wave.open(out_wav, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(AUDIO_RATE)
        for a, b in frames:
            s, e = int(round(a * AUDIO_RATE / fps)), int(round(b * AUDIO_RATE / fps))
            chunk = audio[s:e].astype(np.float32)
            if len(chunk) < e - s:  # video longer than audio: pad with silence
                chunk = np.vstack([chunk, np.zeros((e - s - len(chunk), 2), np.float32)])
            if len(chunk) > 2 * fade_n:
                chunk[:fade_n] *= ramp
                chunk[-fade_n:] *= ramp[::-1]
            w.writeframes(np.clip(chunk, -32768, 32767).astype("<i2").tobytes())


def _filter_option(script_path: str) -> list[str]:
    """`-/filter_complex file` on FFmpeg >= 7, `-filter_complex_script` before."""
    out = subprocess.run([ffmpeg_exe(), "-hide_banner", "-version"],
                         capture_output=True, text=True).stdout
    m = re.search(r"ffmpeg version n?(\d+)", out)
    if m and int(m[1]) >= 7:
        return ["-/filter_complex", script_path]
    return ["-filter_complex_script", script_path]


def render(info: MediaInfo, intervals, out_path: str, progress=lambda p: None) -> str:
    if not info.has_video:
        raise RuntimeError("This file has no video track.")
    frames = snap_to_frames(intervals, info.fps)
    if not frames:
        raise RuntimeError("Nothing to render: every part of the video is cut.")
    total = sum(b - a for a, b in frames) / info.fps

    with tempfile.TemporaryDirectory(prefix="ppe-") as tmp:
        progress(0.01)
        wav = os.path.join(tmp, "audio.wav")
        _write_audio(info.path, frames, info.fps, wav)
        progress(0.08)

        expr = "+".join(f"between(n,{a},{b - 1})" for a, b in frames)
        script = os.path.join(tmp, "filter.txt")
        with open(script, "w") as f:
            f.write(f"[0:v]fps={info.fps_expr},select='{expr}',setpts=N/FRAME_RATE/TB[v]")

        log_path = os.path.join(tmp, "ffmpeg.log")
        cmd = [ffmpeg_exe(), "-y", "-hide_banner", "-nostats", "-i", info.path, "-i", wav,
               *_filter_option(script), "-map", "[v]", "-map", "1:a",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
               "-progress", "pipe:1", out_path]
        with open(log_path, "w") as log:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=log, text=True)
            for line in proc.stdout:
                if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                    try:
                        t = int(line.split("=", 1)[1]) / 1e6
                        progress(0.08 + 0.92 * min(t / total, 1.0))
                    except ValueError:
                        pass
            proc.wait()
        if proc.returncode != 0:
            with open(log_path, errors="replace") as f:
                raise RuntimeError("FFmpeg failed:\n" + f.read()[-1500:])
    progress(1.0)
    return out_path


def output_path_for(src: str) -> str:
    stem, _ = os.path.splitext(src)
    out, i = f"{stem} (edited).mp4", 2
    while os.path.exists(out):
        out, i = f"{stem} (edited {i}).mp4", i + 1
    return out
