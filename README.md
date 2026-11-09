# Pre-production Editor

A personal auto-editor for talking-head videos. Edit **while you film** using claps,
then let the app cut pauses and bad takes. MP4 in, MP4 out. Runs entirely on your
computer.

## How to film

| Sound | Meaning |
|---|---|
| 👏👏 Double clap | **New bit starts here.** Also how you start: nothing before your first clap is kept. |
| 👏 Single clap | **Redo.** Throws away everything since the last clap. Just say the line again. |
| (no clap) | Keep going. The last take wins. |

```
(set up camera) 👏👏 intro… 👏 intro again… 👏 intro (good!) 👏👏 next point… 👏 next point (good!) …
                └──────── result: "intro (good!)" + "next point (good!)" ───────┘
```

Tips: clap in a pause (not over your words), close-ish to the mic, and leave a
beat of silence after clapping.

## Setup (once)

Requires Python 3.10+.

```bash
python -m venv .venv
.venv\Scripts\activate        # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

FFmpeg is included through `imageio-ffmpeg`. If you already have FFmpeg installed,
that one is used instead.

## Run

```bash
python -m ppe
```

Your browser opens at http://127.0.0.1:8765. Choose a video, then review:

- **Timeline**: green = kept, grey = pause, red = retake, yellow = clap,
  brown = before first clap, purple = your manual edits.
- **Click** to jump; **double-click** a segment (or press `X`) to flip it kept/cut.
- **Drag** across the timeline to select any stretch, then press `K` to keep it (e.g. a
  quiet word that was cut) or `X` to cut it. `Ctrl+Z` undoes any edit.
- **Claps list**: set a false detection (door slam, desk thump) to *ignore*, or add a
  clap the detector missed at the playhead.
- **Preview edit** (`P`) plays the video skipping everything that will be cut.
- **Sliders**: silence threshold, minimum pause length (default 0.2 s), breathing room
  (default 0.1 s), clap sensitivity, double-clap gap.
- **Cut breaths & noises in pauses** (on by default): short sounds with no voice pitch
  (breaths, thumps, lip noises) count as part of the pause.
- **Render MP4** saves `<name> (edited).mp4` next to the original.

## How it works

- **Pauses:** loudness is measured every 10 ms. Quiet stretches longer than the
  minimum pause are cut, leaving breathing room either side. A short burst of sound
  (under 0.3 s) with almost no voice pitch counts as pause too, so a quiet, deep word
  is kept but a breath or desk thump is not.
- **Claps:** a sound counts as a clap if it is loud, starts suddenly, is gone within
  ~150 ms (a word keeps going), and is noise-like and bright (a voice is tonal; a
  thump is low). No ML.
- **Render:** cuts snap to video frames; video is cut with FFmpeg and the audio is
  rebuilt with tiny fades at each cut so nothing clicks and A/V stay in sync.

## Development

```bash
pip install pytest
python -m pytest
```

See [SPEC.md](SPEC.md) for the full spec and post-MVP ideas (voice commands, face lock,
timeline export).
