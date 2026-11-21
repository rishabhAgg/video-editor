"""Timestamp-based stream-copy and accurate splits."""

import json
import warnings
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ..exceptions import VideoToolError
from ..utils.paths import output_paths
from ..utils.timecode import ffmpeg_time, frame_timestamps, parse_timecode, positive_rate
from .audio import split_audio_options
from .encode import check_aspect, encode_options, render
from .probe import probe
from .subtitles import split_subtitle_options

FAST_MODE_WARNING = "Fast splits use stream copy: cuts depend on keyframes and may not be frame-accurate."


def _segments(path: Path) -> list[dict]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise VideoToolError(f"Could not read segments from {path}: {exc}") from exc
    entries = document.get("segments") if isinstance(document, dict) else None
    if not isinstance(entries, list) or not entries:
        raise VideoToolError("Expected a nonempty 'segments' array")
    if any(not isinstance(entry, dict) for entry in entries):
        raise VideoToolError("Each segment must be an object with name, start and end values")
    return entries


def split(
    source: Path, segments: Path, output_dir: Path, *, mode: str = "fast", preset: str | None = None
) -> list[Path]:
    """Split a source into named files, returning outputs in manifest order."""
    if mode not in {"fast", "accurate"}:
        raise VideoToolError(f"Unknown split mode: {mode}")
    if mode == "fast" and preset is not None:
        raise VideoToolError("A preset is only used by accurate split mode")
    entries = _segments(segments)
    info = probe(source)
    duration = Decimal(str(info.duration))
    if not duration.is_finite() or duration <= 0:
        raise VideoToolError("Source duration is unknown")
    source_path = Path(info.path)
    suffix = source_path.suffix
    if not suffix or (mode == "accurate" and suffix.lower() not in {".mp4", ".mov", ".mkv"}):
        suffix = ".mkv"

    names: set[str] = set()
    requests = []
    fps = positive_rate(info.fps) or positive_rate(info.average_fps)
    for entry in entries:
        name = entry.get("name")
        if (
            not isinstance(name, str) or not name.strip() or name in {".", ".."}
            or any(ch in name for ch in "/\\\x00\r\n")
        ):
            raise VideoToolError(f"Invalid segment name: {name!r}")
        if name.casefold() in names:
            raise VideoToolError(f"Duplicate segment name: {name!r}")
        names.add(name.casefold())
        output, _ = output_paths(output_dir / f"{name}{suffix}")
        start = parse_timecode(entry.get("start"), fps)
        end = parse_timecode(entry.get("end"), fps)
        requests.append((output, start, end))
    origin = info.metadata.get("format", {}).get("start_time", "0")
    try:
        start_time = Decimal(str(origin))
    except InvalidOperation:
        start_time = Decimal(0)
    if not start_time.is_finite():
        start_time = Decimal(0)
    indices = {bound for _, start, end in requests for bound in (start, end) if isinstance(bound, int)}
    timestamps = frame_timestamps(source_path, indices, start_time=start_time)
    resolved = []
    for output, start, end in requests:
        start = timestamps[start] if isinstance(start, int) else start
        end = timestamps[end] if isinstance(end, int) else end
        if not 0 <= start < end <= duration:
            raise VideoToolError(f"Invalid boundaries for {output.stem!r}: require 0 <= start < end <= {duration}")
        resolved.append((output, start, end))

    if mode == "fast":
        warnings.warn(FAST_MODE_WARNING, UserWarning, stacklevel=2)
        options = ["-map", "0", "-c", "copy", "-avoid_negative_ts", "make_zero"]
    else:
        check_aspect(preset, (info.width, info.height))
        options = [
            "-map", "0:V:0", *split_audio_options(), *split_subtitle_options(suffix),
            *encode_options(info, preset), "-fps_mode:v", "passthrough", "-enc_time_base:v", info.time_base,
        ]
    return [
        render(
            ["-ss", ffmpeg_time(start), "-i", str(source_path), "-t", ffmpeg_time(end - start), *options],
            output, [info], duration=float(end - start),
            warnings=(FAST_MODE_WARNING,) if mode == "fast" else (),
        )
        for output, start, end in resolved
    ]
