"""Synthetic-audio tests for pause removal and clap retakes.

"Speech" is a buzzy harmonic tone in word-length bursts; a "clap" is a short,
bright noise burst that dies away in ~20 ms; a "thump" is a loud low-frequency
burst that should NOT count as a clap.
"""
import numpy as np

from ppe.analysis import AudioAnalysis, Settings
from ppe.render import snap_to_frames

SR = 16000
rng = np.random.default_rng(0)


def speech(seconds):
    t = np.arange(int(seconds * SR)) / SR
    tone = sum(np.sin(2 * np.pi * 140 * k * t) / k for k in range(1, 8))
    words = (np.sin(2 * np.pi * 3 * t) > -0.7).astype(float)  # tiny gaps inside
    env = np.minimum(1, np.minimum(t, t[::-1]) / 0.02)
    return 0.08 * tone * (0.4 + 0.6 * words) * env


def silence(seconds):
    return np.zeros(int(seconds * SR))


def clap():
    n = int(0.06 * SR)
    return 0.9 * rng.standard_normal(n) * np.exp(-np.arange(n) / (0.012 * SR))


def thump():
    n = int(0.15 * SR)
    t = np.arange(n) / SR
    return 0.9 * np.sin(2 * np.pi * 60 * t) * np.exp(-t / 0.03)


def build(parts):
    """parts: list of (label, array). Returns (samples, {label: (start, end)})."""
    out, marks, pos = [], {}, 0
    for label, a in parts:
        if label:
            marks[label] = (pos / SR, (pos + len(a)) / SR)
        out.append(a)
        pos += len(a)
    x = np.concatenate(out)
    x += 0.0005 * rng.standard_normal(len(x))  # room noise
    return x.astype(np.float32), marks


def kept_fraction(result, start, end):
    total = 0.0
    for a, b in result["kept"]:
        total += max(0.0, min(b, end) - max(a, start))
    return total / (end - start)


def scenario():
    gap = lambda: silence(0.8)
    return build([
        (None, silence(0.5)), ("preroll", speech(1.5)), (None, silence(1.0)),
        (None, clap()), (None, silence(0.25)), (None, clap()), (None, gap()),  # 👏👏
        ("intro1", speech(2.0)), (None, gap()), (None, clap()), (None, gap()),                     # 👏
        ("intro2", speech(2.0)), (None, gap()), (None, clap()), (None, gap()),                     # 👏
        ("intro3", speech(2.0)), (None, gap()), (None, clap()), (None, silence(0.3)), (None, clap()), (None, gap()),
        ("main1", speech(3.0)), (None, gap()), (None, clap()), (None, gap()),
        ("main2a", speech(2.0)), ("pause", silence(1.0)), ("main2b", speech(2.0)), (None, silence(1.0)),
    ])


def test_clap_retakes_last_take_wins():
    x, m = scenario()
    res = AudioAnalysis(x, SR).build(Settings())
    types = [e["type"] for e in res["events"]]
    assert types == ["double", "single", "single", "double", "single"], types
    for gone in ("preroll", "intro1", "intro2", "main1"):
        assert kept_fraction(res, *m[gone]) < 0.05, gone
    for kept in ("intro3", "main2a", "main2b"):
        assert kept_fraction(res, *m[kept]) > 0.9, kept


def test_pause_removed_with_breathing_room():
    x, m = scenario()
    res = AudioAnalysis(x, SR).build(Settings(min_pause=0.2, padding=0.1))
    s, e = m["pause"]
    kept = kept_fraction(res, s, e) * (e - s)
    assert 0.15 <= kept <= 0.25, kept  # ~0.1 s each side


def test_claps_are_cut():
    x, _ = scenario()
    res = AudioAnalysis(x, SR).build(Settings())
    for ev in res["events"]:
        assert kept_fraction(res, ev["t"] - 0.01, ev["t_end"] + 0.05) == 0


def test_thump_is_not_a_clap():
    x, _ = build([(None, silence(1)), (None, speech(2)), (None, silence(0.8)), (None, thump()),
                  (None, silence(0.8)), (None, speech(2)), (None, silence(1))])
    res = AudioAnalysis(x, SR).build(Settings())
    assert res["events"] == []


def test_words_are_not_claps_and_no_claps_keeps_everything():
    x, _ = build([(None, silence(0.5)), ("talk", speech(5)), (None, silence(0.5))])
    a = AudioAnalysis(x, SR)
    assert a.detect_claps() == []
    res = a.build(Settings())
    assert res["stats"]["no_claps"]
    assert res["stats"]["edited"] > 4.5


def test_ignore_edit_and_manual_override():
    x, m = scenario()
    a = AudioAnalysis(x, SR)
    first_single = a.build(Settings())["events"][1]
    res = a.build(Settings(), event_edits={first_single["key"]: "ignore"})
    # ignoring the clap after intro1 merges intro1 into the take thrown away by the next clap
    assert kept_fraction(res, *m["intro1"]) < 0.05
    res = a.build(Settings(), overrides=[{"start": m["intro1"][0], "end": m["intro1"][1], "keep": True}])
    assert kept_fraction(res, *m["intro1"]) > 0.95


def test_added_clap():
    x, m = scenario()
    a = AudioAnalysis(x, SR)
    # a single clap right after main2a throws main2a away
    res = a.build(Settings(), added_events=[{"t": m["pause"][0] + 0.4, "type": "single"}])
    assert kept_fraction(res, *m["main2a"]) < 0.05
    assert kept_fraction(res, *m["main2b"]) > 0.9


def test_snap_to_frames_merges_and_rounds():
    assert snap_to_frames([(0.0, 1.0), (1.01, 2.0), (3.0, 3.001)], 30) == [(0, 60)]
