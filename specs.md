# Local HDR Video Toolkit — Implementation Specification

## 1. Objective

Build a local-first Python video-processing toolkit for editing PS5 gameplay recordings and producing:

1. Long-form YouTube videos.
2. Vertical Instagram Reels.
3. Short highlight clips.
4. Merged gameplay recordings.
5. Timestamp-based video segments.

The application must run entirely on the local Mac. It must not require a backend server, cloud services, accounts, or a database.

The primary interface is a CLI. A lightweight native desktop crop editor will provide visual control over vertical framing.

### Non-negotiable requirements

- Preserve original recordings.
- Never overwrite source files.
- Preserve HDR metadata whenever the operation permits it.
- Use 10-bit HDR encoding for HDR exports.
- Support HDR10/PQ source footage.
- Support dynamic crop keyframes.
- Support blurred-background vertical output.
- Provide a visual crop preview.
- Detect incompatible media before merging.
- Validate exported video metadata.
- Never silently convert HDR footage to SDR.
- Never silently claim that an output is HDR when its pixel data or metadata is SDR.

---

# 2. Technology stack

| Component | Technology |
|---|---|
| Language | Python 3.11+ |
| Video processing | FFmpeg |
| Metadata inspection | FFprobe |
| Desktop UI | PySide6 |
| Frame extraction | OpenCV |
| Image processing | NumPy |
| Configuration | JSON |
| Testing | pytest |
| Logging | Python logging |

Use FFmpeg as a subprocess. Do not implement video encoding manually in Python.

Use `subprocess.run` or `subprocess.Popen` with argument arrays. Never construct shell commands from untrusted filenames.

Do not require administrator permissions. The application and Python dependencies must be installable inside a project virtual environment.

---

# 3. Project structure

Create the following structure:

```text
local-video-tool/
├── README.md
├── pyproject.toml
├── requirements.txt
├── video_tool.py
│
├── src/
│   └── video_tool/
│       ├── __init__.py
│       ├── cli.py
│       ├── config.py
│       ├── models.py
│       ├── exceptions.py
│       │
│       ├── media/
│       │   ├── probe.py
│       │   ├── metadata.py
│       │   ├── validation.py
│       │   ├── split.py
│       │   ├── merge.py
│       │   ├── encode.py
│       │   ├── crop.py
│       │   ├── audio.py
│       │   └── subtitles.py
│       │
│       ├── crop/
│       │   ├── keyframes.py
│       │   ├── interpolation.py
│       │   ├── geometry.py
│       │   └── preview.py
│       │
│       ├── ui/
│       │   ├── main_window.py
│       │   ├── video_view.py
│       │   ├── crop_overlay.py
│       │   ├── timeline.py
│       │   └── export_dialog.py
│       │
│       └── utils/
│           ├── ffmpeg.py
│           ├── paths.py
│           ├── timecode.py
│           └── logging.py
│
├── config/
│   ├── settings.json
│   └── export_presets.json
│
├── inputs/
├── output/
│   ├── long-form/
│   ├── reels/
│   └── clips/
├── projects/
├── previews/
├── logs/
│
└── tests/
    ├── test_probe.py
    ├── test_metadata.py
    ├── test_split.py
    ├── test_merge.py
    ├── test_crop_geometry.py
    ├── test_keyframes.py
    ├── test_hdr_validation.py
    └── test_export.py
```

Keep the architecture modular. The CLI and GUI must use the same processing services.

---

# 4. Media inspection and HDR detection

Implement an FFprobe-based media inspection service.

Example:

```bash
ffprobe -v error \
  -show_format \
  -show_streams \
  -of json \
  input.mp4
```

Capture:

### Video properties

- Width and height.
- Frame rate as a rational number.
- Average frame rate.
- Duration.
- Codec.
- Profile.
- Pixel format.
- Bit depth.
- Time base.
- Start time.
- Frame count when available.
- Colour primaries.
- Transfer characteristics.
- Matrix coefficients.
- Colour range.
- Chroma subsampling.

### HDR properties

- BT.2020 colour primaries.
- SMPTE ST 2084/PQ transfer function.
- HLG transfer function.
- Mastering display metadata.
- MaxCLL.
- MaxFALL.
- HDR10+ metadata if detectable.
- Dolby Vision metadata if detectable.

Use a structured model:

```python
@dataclass
class MediaInfo:
    path: str
    duration: float
    width: int
    height: int
    fps: str
    video_codec: str
    pixel_format: str
    bit_depth: int
    color_primaries: str | None
    color_transfer: str | None
    color_space: str | None
    is_hdr: bool
    hdr_format: str | None
    audio_streams: list
    video_streams: list
    metadata: dict
```

Do not classify media as HDR merely because its filename contains HDR.

A likely HDR10 source has:
- 10-bit video.
- BT.2020 primaries.
- SMPTE ST 2084 transfer characteristics.
- BT.2020 non-constant-luminance matrix.

If metadata is incomplete or contradictory, classify it as `UNKNOWN`, not SDR or HDR by assumption.

Show a clear warning for unknown HDR metadata.

Save a complete source metadata snapshot in each project.

---

# 5. Splitting videos

Support timestamp-based splitting.

CLI:

```bash
python video_tool.py split \
  --input inputs/gta6.mp4 \
  --segments projects/gta6/segments.json
```

Example `segments.json`:

```json
{
  "segments": [
    {
      "name": "mission-start",
      "start": "00:02:15.000",
      "end": "00:08:30.000"
    },
    {
      "name": "police-chase",
      "start": "00:14:20.000",
      "end": "00:19:45.000"
    }
  ]
}
```

Requirements:

- Support HH:MM:SS.milliseconds.
- Support frame-based timecode when FPS is known.
- Validate start and end boundaries.
- Reject negative durations.
- Reject timestamps beyond source duration.
- Support multiple segments.
- Preserve audio and subtitles when possible.
- Preserve source HDR metadata.

Provide two modes.

### Fast mode

Use stream copy:

```bash
ffmpeg -ss 00:02:15.000 \
  -i input.mp4 \
  -t 375 \
  -map 0 \
  -c copy \
  -avoid_negative_ts make_zero \
  output.mp4
```

Fast mode must clearly explain that cuts can be limited by keyframe positions and may not be frame-accurate.

### Accurate mode

Decode and re-encode the segment using a configured codec and quality preset.

For HDR input, use a 10-bit HDR-compatible pipeline.

Do not apply SDR filters or tone mapping.

Use frame timestamps rather than assuming every frame is exactly 1/FPS seconds apart.

---

# 6. Merging videos

CLI:

```bash
python video_tool.py merge \
  --input inputs/part1.mp4 inputs/part2.mp4 inputs/part3.mp4 \
  --output output/long-form/full-gameplay.mp4
```

Before merging, inspect every input.

Compare:

- Video codec.
- Resolution.
- Frame rate.
- Time base.
- Pixel format.
- Bit depth.
- Audio codec and sample rate.
- Number of audio streams.
- HDR transfer characteristics.
- Colour primaries and matrix.
- HDR metadata compatibility.

### Compatible files

Generate an FFmpeg concat-demuxer list:

```text
file '/absolute/path/part1.mp4'
file '/absolute/path/part2.mp4'
file '/absolute/path/part3.mp4'
```

Run:

```bash
ffmpeg -f concat -safe 0 \
  -i concat_list.txt \
  -map 0 \
  -c copy \
  output.mp4
```

Use stream copy only when compatibility is verified.

### Incompatible files

Offer a normalized re-encode mode.

Do not silently change frame rates or colour properties.

If HDR and SDR sources are mixed, require an explicit decision:

- Reject merge.
- Convert all clips to a common HDR format where technically valid.
- Convert all clips to SDR with explicit tone mapping.

Do not automatically pretend SDR footage is HDR.

For HDR footage, do not assume copying the first clip's metadata is sufficient for all clips. Validate the output.

---

# 7. Dynamic vertical crop engine

This is the most important feature.

Original landscape footage should remain intact in the source archive.

For a 1920 × 1080 source, a full-height 9:16 crop is approximately 608 × 1080.

For a 3840 × 2160 source, it is approximately 1215 × 2160.

The crop must be calculated from the input dimensions and target aspect ratio, not hardcoded.

Support these output modes:

### Mode A: Dynamic crop

Crop a 9:16 viewport from the landscape video.

The viewport's horizontal position changes over time using keyframes.

### Mode B: Blurred background

- Preserve the complete original landscape frame.
- Scale it to fill the 9:16 canvas.
- Apply a configurable blur.
- Add the original landscape footage as a foreground layer.
- Fit the foreground inside the portrait canvas.
- Optionally use a subtle dark overlay behind subtitles.

### Mode C: Dynamic crop with blurred background

Use dynamic crop as the main foreground content, with a blurred full-frame background.

This is useful when the vertical crop does not fill the portrait canvas or when a less aggressive crop is preferred.

Support output dimensions:

- 1080 × 1920.
- 720 × 1280.
- Custom dimensions.

Keep output dimensions even.

---

# 8. Crop geometry

Implement crop geometry independently of FFmpeg.

For a target 9:16 crop:

```python
crop_height = source_height
crop_width = round(crop_height * 9 / 16)
```

If this exceeds source width, use source width and calculate crop height instead.

Represent horizontal position using normalized coordinates:

```json
{
  "center_x": 0.5
}
```

Where:

- 0.0 = far left.
- 0.5 = centre.
- 1.0 = far right.

Calculate:

```python
crop_x = center_x * source_width - crop_width / 2
```

Then:

- Clamp crop_x between 0 and source_width - crop_width.
- Ensure crop_x is an even number for common chroma-subsampled output.
- Keep crop_y and crop dimensions valid.
- Maintain target aspect ratio.

Do not distort the original footage.

---

# 9. Crop keyframes and interpolation

Use a project-level JSON format:

```json
{
  "version": 1,
  "source": "gta6.mp4",
  "output_aspect_ratio": "9:16",
  "output_width": 1080,
  "output_height": 1920,
  "mode": "dynamic_crop",
  "interpolation": "smooth",
  "keyframes": [
    {
      "time": 0.0,
      "center_x": 0.25
    },
    {
      "time": 5.0,
      "center_x": 0.50
    },
    {
      "time": 10.0,
      "center_x": 0.75
    }
  ]
}
```

Implement:

- Linear interpolation.
- Smooth interpolation.
- Hold interpolation.

The default should be smooth interpolation.

The crop must remain within the source boundaries at every frame.

Implement interpolation using presentation timestamps where possible, not only frame indices.

Support multiple crop keyframes.

The crop should move smoothly between positions, without abrupt jumps or overshooting the frame.

---

# 10. Visual crop editor

Implement a lightweight PySide6 desktop interface.

Do not build a full video editor or multi-track timeline in this phase.

### Main interface

The editor should show:

1. Video preview.
2. Landscape source frame.
3. Vertical crop overlay.
4. Crop preview.
5. Timeline with a playhead.
6. Keyframe markers.
7. Crop position controls.
8. Export controls.

### Required interactions

- Open a video file.
- Play and pause.
- Seek through the video.
- Step one frame backward and forward.
- Select 9:16, 1:1 or 16:9.
- Drag the crop rectangle horizontally.
- Drag the crop rectangle vertically when applicable.
- Add a crop keyframe.
- Update an existing keyframe.
- Delete a keyframe.
- Navigate between keyframes.
- Preview interpolation.
- Save project.
- Reopen project.
- Export current crop.

### Crop overlay

Draw the crop rectangle on top of the original landscape frame.

The rectangle must have:

- Visible border.
- Semi-transparent outside region.
- Drag handle.
- Centre indicator.
- Current normalized X/Y coordinates.

Dragging the rectangle must update the current crop position.

Provide a toggle for:

- Show crop boundary.
- Show centre line.
- Show thirds grid.
- Show safe zones for captions.

### Preview architecture

Do not decode full-resolution 4K HDR video for every UI interaction.

Generate a temporary low-resolution preview proxy.

The proxy may be tone-mapped for display on an SDR screen, but the editor must clearly label it as a framing preview, not a colour-accurate HDR preview.

Final rendering must always use the original source.

Preview must show:

- Original landscape.
- Cropped portrait output.
- Blurred-background output.
- Keyframed movement.

Use frame caching to avoid repeatedly decoding the same frame.

### Performance

- Do not preload the entire video into RAM.
- Seek using FFmpeg or a compatible Qt multimedia backend.
- Cache only a limited number of frames.
- Generate previews at reduced resolution.
- Show export progress.
- Allow cancellation safely.

---

# 11. FFmpeg dynamic crop implementation

Generate a crop expression from the keyframes.

The expression should calculate crop X from the current presentation timestamp.

Example conceptual command:

```bash
ffmpeg -i input.mp4 \
  -vf "crop=w=608:h=1080:x='EXPRESSION':y=0" \
  ...
```

The `EXPRESSION` must be generated from the saved keyframes and interpolation settings.

Do not hardcode crop positions.

For dynamic cropping:

1. Compute crop dimensions.
2. Convert normalized keyframe positions into pixel coordinates.
3. Build a piecewise interpolation expression.
4. Clamp positions at frame boundaries.
5. Ensure crop dimensions and positions are compatible with output chroma subsampling.
6. Apply scaling after cropping.
7. Validate the final aspect ratio.

If the generated FFmpeg expression becomes too complex or unreliable, implement a frame-by-frame crop-coordinate renderer using FFmpeg's frame timestamp evaluation or a carefully designed PyAV processing path.

Do not use OpenCV's standard 8-bit image pipeline for final HDR rendering. It may lose HDR precision or metadata.

OpenCV is permitted for UI previews and SDR proxies.

---

# 12. HDR processing and preservation

Treat HDR processing as a first-class pipeline.

Initial supported source profile:

- HDR10.
- HEVC Main 10.
- 10-bit.
- BT.2020 primaries.
- SMPTE ST 2084/PQ transfer.
- BT.2020 non-constant-luminance matrix.

Support HLG detection, but do not claim full HLG export support until tested.

### HDR output profile

Create an initial `hdr10_hevc` export profile.

Use:

- HEVC/H.265.
- 10-bit pixel format: yuv420p10le.
- BT.2020 primaries.
- SMPTE ST 2084 transfer.
- BT.2020 non-constant-luminance matrix.
- Appropriate mastering-display metadata.
- MaxCLL and MaxFALL when known.
- High-quality encoding settings.

Example command skeleton:

```bash
ffmpeg -i input.mp4 \
  -vf "crop=...,scale=1080:1920:flags=lanczos,format=yuv420p10le" \
  -c:v libx265 \
  -profile:v main10 \
  -crf 16 \
  -preset medium \
  -pix_fmt yuv420p10le \
  -color_primaries bt2020 \
  -color_trc smpte2084 \
  -colorspace bt2020nc \
  -tag:v hvc1 \
  -c:a aac \
  -b:a 320k \
  output.mp4
```

This is a starting command, not a complete universal HDR recipe.

The implementation must inspect source metadata and pass through valid HDR mastering and content-light metadata when supported by the source, encoder and muxer.

Do not fabricate mastering-display metadata or MaxCLL/MaxFALL values.

Use FFmpeg's supported encoder options and verify actual output metadata.

For libx265, investigate the appropriate `hdr-opt`, `master-display` and `max-cll` parameters. Do not assume that `-map_metadata 0` preserves HDR metadata embedded in the HEVC bitstream.

For stream-copy operations, preserve original encoded data and metadata whenever possible.

### HDR export rules

- Do not tone-map HDR to SDR unless the user explicitly chooses SDR.
- Do not relabel SDR footage as HDR.
- Do not silently discard HDR metadata.
- Do not silently change PQ to HLG.
- Do not claim Dolby Vision preservation when only HDR10 is retained.
- Record source and output metadata in an export report.
- Fail or warn if HDR metadata cannot be preserved as configured.

### Instagram and YouTube

Create two HDR-oriented profiles:

`youtube_hdr10`:
- HEVC Main 10.
- 10-bit.
- Rec. 2020.
- PQ.
- HDR metadata.
- 16:9 for long-form.

`instagram_hdr`:
- 9:16.
- HEVC Main 10.
- 10-bit.
- HDR-compatible colour metadata.
- PQ HDR10 as the initial export target.

Instagram has platform-specific HDR ingestion and transcoding behaviour. Do not promise that every HDR10 file will remain HDR after upload. The application must clearly label