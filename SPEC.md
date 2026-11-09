# Pre-production Editor — Spec

A personal auto-editor for talking-head videos. You do the editing *while filming*
(claps for retakes), then the app cuts the video for you. MP4 in, MP4 out.

## MVP scope

1. **Pause removal** — cut silences, YouTube-fast.
2. **Clap retakes** — single/double clap commands (rules below).
3. **Review screen** — see what will be cut, fix mistakes, then one click to render.
4. **Rendered MP4** — ready to upload.

Not in MVP: voice commands, effects, music, colour grades, project/timeline export
(Premiere/Resolve), other output formats, live processing.

## Clap rules

| Sound | Meaning |
|---|---|
| Double clap 👏👏 | **Checkpoint** — "a new bit starts here." |
| Single clap 👏 | **Redo** — throw away everything since the last clap; go again. |
| No clap | Keep going; the last take is kept. |

- **Nothing before the first clap is kept** — you clap to start. (If no claps are
  found at all, everything is kept and the review screen says so.) The pre-roll can
  be flipped back on in the review screen like any other segment.
- Two claps closer than the *double-clap gap* (default 0.6 s) = a double clap.
- Claps themselves are always cut out.
- Equivalent rule used in code: the stretch of recording *before* each single clap
  (back to the previous clap) is discarded; everything else is kept.

Example: `(fiddling with camera) 👏👏 intro1 👏 intro2 👏 intro3 👏👏 main1 👏 main2` → `intro3 + main2`.

## Pause removal

- Any stretch quieter than the **silence threshold** and longer than
  **min pause** (default 0.2 s) is cut, leaving **breathing room** (default 0.1 s)
  on each side of speech.
- Silence threshold defaults to an automatic value based on the recording's noise
  floor and speech level.

## Review screen (local web page)

- Video player + timeline (waveform, coloured by keep / pause / retake / clap).
- Sliders: silence threshold (or auto), min pause, breathing room, clap sensitivity,
  double-clap gap. Changes re-analyse instantly.
- Click a segment to jump there; double-click (or `X`) to flip it between kept and cut.
- Clap list: change any clap to single / double / ignore (fixes door slams and
  misdetections); add a clap at the playhead (fixes missed claps).
- "Preview edit" plays the video skipping everything that will be cut.
- Stats: original length → edited length.
- **Render MP4** → `<name> (edited).mp4` next to the original.

## Tech

- Python 3 + Flask, serving a page on `localhost`. Nothing leaves the machine.
- numpy for audio analysis (no ML).
- FFmpeg for decoding and rendering (system FFmpeg if installed, otherwise the
  bundled one from `imageio-ffmpeg`).
- Cuts are snapped to video frames and audio is assembled with tiny fades, so
  audio/video stay in sync and cuts don't click.

## Assumptions

- Video file has its own (synced) audio track. Phone or webcam.
- Recordings of 5–40 minutes; laptop CPU only.

## After the MVP (ideas)

- Voice commands ("Cue: fade out", zoom, captions, B-roll markers, mood music,
  colour grade) via transcription.
- Export a timeline for a real editor.
- Face lock: film slightly wide, then auto-crop/zoom each segment so your face stays
  in the same spot across cuts (makes jump cuts much less choppy).
