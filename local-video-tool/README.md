# Local HDR Video Toolkit

A standalone, local-first CLI and macOS crop editor for gameplay recordings. No account, network service, or database is used at runtime. The original recordings are never modified.

## Setup

Use Python 3.11+ and put `ffmpeg` and `ffprobe` on `PATH`. FFmpeg needs `libx265` and `libx264` for HDR10 and SDR encoding respectively. Explicit HDR-to-SDR conversion additionally requires perceptual `swscale` (available in FFmpeg 9) or a build with `libzimg` (`zscale`). No administrator privileges are required by the toolkit.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

For CLI only, run `pip install -e .` instead. Use `video-tool` or `python video_tool.py` from the project.

## Workflows

```bash
video-tool probe inputs/recording.mp4
video-tool split --input inputs/recording.mp4 --segments projects/segments.json --mode fast
video-tool merge --input inputs/part1.mp4 inputs/part2.mp4 --output output/long-form/merged.mp4
video-tool editor inputs/recording.mp4
video-tool crop --project projects/reel.json --output output/reels/reel.mp4 --preset instagram_hdr
```

A segments file looks like this:

```json
{"segments": [{"name": "highlight", "start": "00:00:05.000", "end": "00:00:20.000"}]}
```

Fast split preserves encoded streams but cuts may start at keyframes. Use `--mode accurate` to decode and cut using source timestamps. With known FPS, segment boundaries can also be non-drop-frame `HH:MM:SS:FF` labels or zero-based frame numbers; both resolve to presentation timestamps.

Stream-copy merge rejects incompatible inputs. Use `--normalize` and set any needed target dimensions and frame rate explicitly with `--size WIDTHxHEIGHT` and `--fps RATE`. Mixing SDR and HDR is rejected unless you opt in to tone mapping with `--normalize --color sdr`. SDR is not relabeled as HDR.

The crop editor saves version 1 JSON projects containing a source metadata snapshot. Select an aspect ratio and crop mode, position the framing rectangle, and add keyframes along the timeline. Play or step through the framing before export. The low-resolution proxy is for framing only; exports decode the original media. Crop supports moving framing, blurred borders with the full landscape image, and moving framing over a blurred background. The preview is not an HDR color reference.

## HDR verification

PQ exports use HEVC Main 10 with BT.2020 signaling and retain detected valid static mastering and content-light metadata. Missing values are not fabricated. HLG, Dolby Vision, and HDR10+ are detected where supported but not re-encoded. HDR status with incomplete or conflicting color tags is reported as `UNKNOWN`; such input is not re-encoded. Dynamic metadata detection is limited to stream side data and sampled opening frames, not a full-frame scan.

Every completed export includes `<output>.report.json` with source and output metadata, validation status, and any warnings. Validation checks dimensions, encoded precision, and detected color signaling, not creative grade or platform ingest. YouTube and Instagram may transcode or downgrade HDR after upload. Existing video and report paths are never overwritten.

Run tests with `pytest`; media integration tests use local FFmpeg and temporary generated fixtures.
