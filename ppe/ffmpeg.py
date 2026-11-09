"""Locate FFmpeg, probe media files and decode audio."""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

# Common frame rates; measured rates close to one of these get snapped to it.
_STANDARD_FPS = {
    23.976: "24000/1001", 24.0: "24", 25.0: "25", 29.97: "30000/1001", 30.0: "30",
    48.0: "48", 50.0: "50", 59.94: "60000/1001", 60.0: "60", 120.0: "120",
}


@lru_cache(maxsize=1)
def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "FFmpeg not found. Run `pip install imageio-ffmpeg` or install FFmpeg."
        ) from e


@dataclass
class MediaInfo:
    path: str
    duration: float
    fps: float          # numeric frame rate
    fps_expr: str       # exact frame rate for ffmpeg filters, e.g. "30000/1001"
    has_video: bool
    has_audio: bool


def _snap_fps(fps: float) -> tuple[float, str]:
    std = min(_STANDARD_FPS, key=lambda s: abs(fps - s))
    if abs(fps - std) < 0.3:
        expr = _STANDARD_FPS[std]
        num, _, den = expr.partition("/")
        return float(num) / float(den or 1), expr
    fps = round(fps, 3)
    return fps, f"{fps}"


def probe(path: str) -> MediaInfo:
    """Read duration / frame rate / streams from `ffmpeg -i` output."""
    proc = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-i", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    err = proc.stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if not m:
        raise RuntimeError(f"Could not read this file as media:\n{err[-800:]}")
    duration = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])

    video_lines = [l for l in err.splitlines() if "Stream #" in l and "Video:" in l
                   and "attached pic" not in l]
    has_audio = any("Stream #" in l and "Audio:" in l for l in err.splitlines())
    fps = 30.0
    if video_lines:
        fm = re.search(r"([\d.]+)\s*fps", video_lines[0]) or re.search(r"([\d.]+)\s*tbr", video_lines[0])
        if fm:
            fps = float(fm[1])
    fps, fps_expr = _snap_fps(fps)
    return MediaInfo(path, duration, fps, fps_expr, bool(video_lines), has_audio)


def decode_audio(path: str, sample_rate: int, channels: int = 1) -> np.ndarray:
    """Decode the audio track to int16 samples, shape (n,) or (n, channels)."""
    proc = subprocess.run(
        [ffmpeg_exe(), "-v", "error", "-i", path, "-vn", "-ac", str(channels),
         "-ar", str(sample_rate), "-f", "s16le", "-"],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError("Could not decode audio:\n" + proc.stderr.decode(errors="replace")[-800:])
    data = np.frombuffer(proc.stdout, dtype=np.int16)
    if channels > 1:
        data = data[: len(data) // channels * channels].reshape(-1, channels)
    return data
