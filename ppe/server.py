"""Local web server for the review screen. Runs on localhost only."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import traceback

from flask import Flask, jsonify, request, send_file, send_from_directory

from .analysis import SAMPLE_RATE, WAVE_BIN, AudioAnalysis, Settings
from .ffmpeg import decode_audio, probe
from .render import output_path_for, render

STATIC = os.path.join(os.path.dirname(__file__), "static")
app = Flask(__name__, static_folder=STATIC, static_url_path="/static")


class Session:
    info = None
    analysis: AudioAnalysis | None = None
    job = {"state": "idle"}


S = Session()
lock = threading.Lock()

_PICKER = r"""
import tkinter as tk
from tkinter import filedialog
root = tk.Tk(); root.withdraw(); root.attributes('-topmost', True)
print(filedialog.askopenfilename(title='Choose a video', filetypes=[
    ('Video', '*.mp4 *.mov *.m4v *.mkv *.webm'), ('All files', '*.*')]))
"""


@app.errorhandler(Exception)
def _error(e):
    traceback.print_exc()
    return jsonify(error=str(e)), 500


@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.post("/api/browse")
def browse():
    out = subprocess.run([sys.executable, "-c", _PICKER], capture_output=True, text=True)
    return jsonify(path=out.stdout.strip())


@app.post("/api/open")
def open_video():
    path = (request.json or {}).get("path", "").strip().strip('"')
    if not os.path.isfile(path):
        return jsonify(error=f"File not found: {path}"), 400
    info = probe(path)
    if not info.has_audio:
        return jsonify(error="This video has no audio track."), 400
    analysis = AudioAnalysis(decode_audio(path, SAMPLE_RATE), SAMPLE_RATE)
    with lock:
        S.info, S.analysis, S.job = info, analysis, {"state": "idle"}
    return jsonify(
        name=os.path.basename(path), path=path, duration=analysis.duration, fps=info.fps,
        has_video=info.has_video, waveform=[round(float(x), 3) for x in analysis.waveform],
        wave_bin=WAVE_BIN, auto_silence_db=analysis.auto_silence_db,
    )


@app.get("/api/video")
def video():
    if not S.info:
        return jsonify(error="No video open"), 404
    return send_file(S.info.path, mimetype="video/mp4", conditional=True)


def _build(body: dict) -> dict:
    if not S.analysis:
        raise RuntimeError("No video open")
    return S.analysis.build(
        Settings.from_dict(body.get("settings")), body.get("event_edits"),
        body.get("added_events"), body.get("overrides"),
    )


@app.post("/api/analyze")
def analyze():
    with lock:
        return jsonify(_build(request.json or {}))


@app.post("/api/render")
def start_render():
    with lock:
        if S.job.get("state") == "running":
            return jsonify(error="Already rendering"), 409
        kept = _build(request.json or {})["kept"]
        info = S.info
        out = output_path_for(info.path)
        S.job = {"state": "running", "progress": 0.0, "output": out}

    def work():
        def progress(p):
            S.job["progress"] = round(p, 3)
        try:
            render(info, kept, out, progress)
            S.job = {"state": "done", "progress": 1.0, "output": out}
        except Exception as e:
            traceback.print_exc()
            S.job = {"state": "error", "error": str(e), "output": out}

    threading.Thread(target=work, daemon=True).start()
    return jsonify(S.job)


@app.get("/api/render")
def render_status():
    return jsonify(S.job)


@app.post("/api/reveal")
def reveal():
    path = S.job.get("output")
    if not path or not os.path.exists(path):
        return jsonify(error="Output not found"), 404
    if sys.platform == "win32":
        subprocess.Popen(["explorer", f"/select,{os.path.normpath(path)}"])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path)])
    return jsonify(ok=True)
