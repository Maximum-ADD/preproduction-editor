"""Audio analysis: find pauses and clap commands, decide what to keep.

Everything is decided on a 10 ms grid. Each grid cell gets a *reason*:
keep, silence (pause), retake (thrown-away take) or clap. Manual overrides from
the review screen are applied on top.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SAMPLE_RATE = 16000   # analysis sample rate
HOP = 0.01            # decision grid, seconds
FINE = 0.005          # clap detection resolution, seconds
WAVE_BIN = 0.05       # waveform resolution sent to the UI, seconds

KEEP, SILENCE, RETAKE, CLAP, PREROLL = 0, 1, 2, 3, 4
REASONS = ("keep", "silence", "retake", "clap", "preroll")

CLAP_PRE = 0.05       # cut this much before a clap...
CLAP_POST = 0.25      # ...and this much after (clap ring-out)
MIN_KEPT = 0.06       # kept blips shorter than this are treated as pause

# Clap tests (see detect_claps). Tuned on a real webcam recording: claps were
# 5-15 ms long and 16-33 dB louder than the voice; lip smacks, plate and desk
# bangs were all 25 ms+ long. Pitch turned out to be useless, so it isn't used.
CLAP_LOUDNESS_DB = 10.0  # at least this much louder than your voice (default)
CLAP_ATTACK_DB = 15.0    # and this much louder than the 10-60 ms before it
CLAP_MAX_MS = 20.0       # falls 10 dB below its peak within this time (claps: 5-15 ms)
CLAP_DECAY_DB = 12.0     # and stays this much quieter from 50 to 150 ms after
                         # (claps: 14+, less when so loud the mic clips)
CLAP_FLATNESS = 0.03     # not a pure tone (only rules out ringing/beeps)


@dataclass
class Settings:
    silence_db: float | None = None   # None = automatic
    min_pause: float = 0.2
    padding: float = 0.1
    clap_sensitivity: float = 0.0     # dB; higher = quieter claps count
    double_gap: float = 0.6

    @classmethod
    def from_dict(cls, d: dict | None) -> "Settings":
        s = cls()
        for k in ("silence_db", "min_pause", "padding", "clap_sensitivity", "double_gap"):
            if d and d.get(k) is not None:
                setattr(s, k, float(d[k]))
        return s


def _db(x):
    return 20.0 * np.log10(np.maximum(x, 1e-7))


def _frame_rms(samples: np.ndarray, size: int) -> np.ndarray:
    n = len(samples) // size
    frames = samples[: n * size].reshape(n, size)
    return np.sqrt(np.einsum("ij,ij->i", frames, frames) / size)


def _runs(mask: np.ndarray):
    """[(start, end), ...] index ranges where mask is True."""
    m = np.concatenate(([0], mask.astype(np.int8), [0]))
    d = np.diff(m)
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


def event_key(t: float) -> str:
    return f"{t:.2f}"


class AudioAnalysis:
    def __init__(self, samples: np.ndarray, sr: int = SAMPLE_RATE):
        if samples.dtype != np.float32:
            samples = samples.astype(np.float32) / 32768.0
        self.sr = sr
        self.samples = samples
        self.duration = len(samples) / sr
        self.rms_db = _db(_frame_rms(samples, int(sr * HOP)))
        self.fine_db = _db(_frame_rms(samples, int(sr * FINE)))

        floor = float(np.percentile(self.rms_db, 10)) if len(self.rms_db) else -90.0
        self.noise_floor = max(floor, -90.0)
        voiced = self.rms_db[self.rms_db > self.noise_floor + 15]
        self.speech_level = float(np.median(voiced)) if len(voiced) > 50 else \
            float(np.percentile(self.rms_db, 90)) if len(self.rms_db) else -20.0
        span = max(self.speech_level - self.noise_floor, 10.0)
        self.auto_silence_db = round(self.noise_floor + 0.4 * span, 1)

        b = int(sr * WAVE_BIN)
        n = len(samples) // b
        peaks = np.abs(samples[: n * b]).reshape(n, b).max(axis=1) if n else np.zeros(0)
        ref = max(float(np.percentile(peaks, 99)) if n else 1.0, 1e-4)
        self.waveform = np.clip(peaks / ref, 0, 1)
        self._clap_cache: dict[float, list[float]] = {}

    # ---- claps -------------------------------------------------------------

    def _spectrum(self, frame: int) -> tuple[float, float]:
        """(spectral centroid Hz, spectral flatness 0..1) around a frame.

        Flatness is the key test: a clap is noise (flat spectrum, ~0.4+),
        a voice is tonal (peaky spectrum, ~0.0).
        """
        start = max(0, int(frame * FINE * self.sr) - 64)
        seg = self.samples[start:start + 512]
        if len(seg) < 128:
            return 0.0, 0.0
        spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
        freqs = np.fft.rfftfreq(len(seg), 1.0 / self.sr)
        centroid = float((spec * freqs).sum() / max(spec.sum(), 1e-12))
        power = spec[freqs >= 100] ** 2 + 1e-12
        flatness = float(np.exp(np.mean(np.log(power))) / np.mean(power))
        return centroid, flatness

    def detect_claps(self, sensitivity: float = 0.0) -> list[float]:
        """Times (s) of individual claps.

        A clap is: much louder than your voice, sudden (big jump over the
        moment before), and very short — it falls away within ~25 ms, while
        lip smacks, words, plates and desk bangs last longer.
        """
        if sensitivity in self._clap_cache:
            return self._clap_cache[sensitivity]
        r = self.fine_db
        level = self.speech_level + CLAP_LOUDNESS_DB - sensitivity
        max_frames = int(round(CLAP_MAX_MS / 1000 / FINE))
        refractory = int(0.1 / FINE)
        hits: list[float] = []
        last = -10 ** 9
        # cheap vectorised pre-filter: loud AND much louder than 10-60 ms before
        pre_max = np.full(len(r), np.inf, np.float32)
        if len(r) > 12:
            win = np.lib.stride_tricks.sliding_window_view(r, 10).max(axis=1)
            pre_max[12:] = win[: len(r) - 12]
        cand = np.flatnonzero((r >= level) & (r - pre_max >= CLAP_ATTACK_DB - 6))
        for i in cand:
            if i < last + refractory:
                continue
            # i = first loud frame (onset), j = peak just after it
            j = int(i + np.argmax(r[i:i + 4]))
            pre = r[max(0, i - 12):max(0, i - 2)]  # skip a frame the onset may straddle
            if len(pre) and r[j] - pre.max() < CLAP_ATTACK_DB:
                continue
            # very short: drops 10 dB below peak within ~25 ms
            after = r[j + 1:j + 1 + max_frames]
            if len(after) and after.min() > r[j] - 10:
                continue
            # and stays down 50-150 ms later (a word or a ringing plate keeps going)
            tail = r[j + 10:j + 30]
            if len(tail) and r[j] - tail.max() < CLAP_DECAY_DB:
                continue
            if self._spectrum(j)[1] < CLAP_FLATNESS:
                continue
            hits.append(round(j * FINE, 3))
            last = j
        self._clap_cache[sensitivity] = hits
        return hits

    @staticmethod
    def group_events(hits: list[float], double_gap: float) -> list[dict]:
        events, i = [], 0
        while i < len(hits):
            if i + 1 < len(hits) and hits[i + 1] - hits[i] <= double_gap:
                events.append({"t": hits[i], "t_end": hits[i + 1], "type": "double"})
                i += 2
            else:
                events.append({"t": hits[i], "t_end": hits[i], "type": "single"})
                i += 1
        return events

    # ---- decisions ---------------------------------------------------------

    def build(self, settings: Settings, event_edits: dict | None = None,
              added_events: list | None = None, overrides: list | None = None) -> dict:
        n = int(np.ceil(self.duration / HOP))
        idx = lambda t: int(min(max(round(t / HOP), 0), n))
        reason = np.zeros(n, np.int8)

        # 1. pauses
        thr = settings.silence_db if settings.silence_db is not None else self.auto_silence_db
        silent = np.ones(n, bool)
        silent[: len(self.rms_db)] = self.rms_db[:n] < thr
        pad = int(round(settings.padding / HOP))
        for s, e in _runs(silent):
            if (e - s) * HOP < settings.min_pause:
                continue
            cs = s if s == 0 else s + pad
            ce = e if e >= n else e - pad
            if ce > cs:
                reason[cs:ce] = SILENCE

        # 2. clap events (detected, then user edits / additions)
        events = self.group_events(self.detect_claps(settings.clap_sensitivity),
                                   settings.double_gap)
        for ev in events:
            ev["key"] = event_key(ev["t"])
            ev["detected_type"] = ev["type"]
            ev["added"] = False
            if event_edits and ev["key"] in event_edits:
                ev["type"] = event_edits[ev["key"]]
        for a in added_events or []:
            t = float(a["t"])
            events.append({"t": t, "t_end": t, "type": a.get("type", "single"),
                           "key": event_key(t), "detected_type": None, "added": True})
        events.sort(key=lambda ev: ev["t"])
        active = [ev for ev in events if ev["type"] in ("single", "double")]
        # the first clap always means "start", whether you clapped once or twice
        if active and active[0]["type"] == "single":
            active[0]["type"] = "double"
            active[0]["note"] = "first clap = start"

        # 3. nothing before the first clap is kept (you clap to start)
        if active:
            reason[: idx(active[0]["t"] - CLAP_PRE)] = PREROLL

        # 4. retakes: the stretch before each single clap is thrown away
        prev_end = 0.0
        for i, ev in enumerate(active):
            if ev["type"] == "single" and i > 0:
                a, b = idx(prev_end), idx(ev["t"] - CLAP_PRE)
                if b > a:
                    reason[a:b] = RETAKE
            prev_end = ev["t_end"] + CLAP_POST

        # 5. the claps themselves
        for ev in active:
            reason[idx(ev["t"] - CLAP_PRE):idx(ev["t_end"] + CLAP_POST)] = CLAP

        # 6. drop tiny kept blips
        for s, e in _runs(reason == KEEP):
            if (e - s) * HOP < MIN_KEPT:
                reason[s:e] = SILENCE

        # 7. manual overrides (later ones win)
        keep = reason == KEEP
        manual = np.zeros(n, bool)
        for o in overrides or []:
            a, b = idx(float(o["start"])), idx(float(o["end"]))
            keep[a:b] = bool(o["keep"])
            manual[a:b] = True

        # 8. run-length encode into segments
        key = reason.astype(np.int32) * 4 + keep * 2 + manual
        bounds = np.concatenate(([0], np.flatnonzero(np.diff(key)) + 1, [n]))
        segments = []
        for s, e in zip(bounds[:-1], bounds[1:]):
            if e <= s:
                continue
            segments.append({
                "start": round(s * HOP, 2), "end": round(min(e * HOP, self.duration), 2),
                "reason": REASONS[reason[s]], "keep": bool(keep[s]), "manual": bool(manual[s]),
            })

        kept = [(round(s * HOP, 2), round(min(e * HOP, self.duration), 2)) for s, e in _runs(keep)]
        cut_by = lambda r: round(float(((reason == r) & ~keep).sum()) * HOP, 2)
        stats = {
            "original": round(self.duration, 2),
            "edited": round(float(keep.sum()) * HOP, 2),
            "pause_cut": cut_by(SILENCE),
            "retake_cut": cut_by(RETAKE),
            "preroll_cut": cut_by(PREROLL),
            "no_claps": not active,
            "singles": sum(ev["type"] == "single" for ev in events),
            "doubles": sum(ev["type"] == "double" for ev in events),
            "pauses": sum(1 for s in segments if s["reason"] == "silence" and not s["keep"]),
        }
        return {
            "segments": segments, "kept": kept, "events": events, "stats": stats,
            "silence_db": thr, "auto_silence_db": self.auto_silence_db,
        }
