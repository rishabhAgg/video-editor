"""Verified concat-demuxer copy and opt-in normalized re-encoding."""

from fractions import Fraction
from math import isfinite
from pathlib import Path
from uuid import uuid4

from ..exceptions import VideoToolError
from ..models import MediaInfo
from ..utils.paths import output_paths
from ..utils.timecode import positive_rate
from .audio import audio_filters
from .encode import check_aspect, encode_options, render, sdr_filter
from .probe import probe
from .validation import color_signature
_STREAM_FIELDS = (
    "codec_type", "codec_name", "profile", "time_base", "extradata_hash",
    "width", "height", "pix_fmt", "bits_per_raw_sample", "sample_aspect_ratio", "field_order",
    "color_primaries", "color_transfer", "color_space", "color_range", "chroma_location",
    "sample_rate", "channels", "channel_layout", "sample_fmt", "attached_pic",
)


def _frame_rate(info: MediaInfo) -> Fraction | None:
    return positive_rate(info.fps) or positive_rate(info.average_fps)


def _stream_signature(info: MediaInfo) -> list[tuple]:
    return [
        (*(
            stream.get(key) if key != "attached_pic" else stream.get("disposition", {}).get(key, 0)
            for key in _STREAM_FIELDS
        ), tuple(entry.get("displaymatrix") for entry in stream.get("side_data_list", []) if "displaymatrix" in entry))
        for stream in info.metadata.get("streams", [*info.video_streams, *info.audio_streams])
    ]


def _copy_differences(first: MediaInfo, other: MediaInfo) -> list[str]:
    fields = ("video_codec", "profile", "width", "height", "time_base", "pixel_format", "bit_depth")
    differences = [field for field in fields if getattr(first, field) != getattr(other, field)]
    if (_frame_rate(first) is None or _frame_rate(first) != _frame_rate(other)
            or positive_rate(first.average_fps) != positive_rate(other.average_fps)):
        differences.append("frame rate")
    if len(first.audio_streams) != len(other.audio_streams):
        differences.append("audio stream count")
    if _stream_signature(first) != _stream_signature(other):
        differences.append("stream layout, codec parameters or codec configuration")
    if color_signature(first) != color_signature(other):
        differences.append("color characteristics or static HDR metadata")
    if first.hdr_format in {"HDR10+", "Dolby Vision"} or other.hdr_format in {"HDR10+", "Dolby Vision"}:
        differences.append("dynamic HDR metadata cannot be verified across concat boundaries")
    return differences


def _concat_line(path: str) -> str:
    if any(char in path for char in "\r\n"):
        raise VideoToolError("Concat input filenames cannot contain line breaks")
    return "file '" + path.replace("'", "'\\''") + "'\n"


def _merge_copy(infos: list[MediaInfo], output: Path) -> Path:
    for other in infos[1:]:
        differences = _copy_differences(infos[0], other)
        if differences:
            raise VideoToolError(f"Incompatible input {other.path}: {', '.join(differences)}; use explicit normalization")
    lines = [_concat_line(info.path) for info in infos]
    output.parent.mkdir(parents=True, exist_ok=True)
    playlist = output.parent / f".video-tool-{uuid4().hex}.ffconcat"
    handle = playlist.open("x", encoding="utf-8", newline="\n")
    try:
        with handle:
            handle.writelines(lines)
        return render(
            ["-f", "concat", "-safe", "0", "-i", str(playlist.resolve()), "-map", "0", "-c", "copy"],
            output, infos, duration=sum(info.duration for info in infos),
        )
    finally:
        playlist.unlink(missing_ok=True)


def _normalize_color(infos: list[MediaInfo], color: str) -> None:
    if color == "sdr":
        for info in infos:
            if info.hdr_format not in {None, "HDR10"}:
                raise VideoToolError(f"Explicit SDR conversion is not supported for {info.hdr_format}: {info.path}")
        return
    if color == "hdr" and any(info.hdr_format != "HDR10" for info in infos):
        raise VideoToolError("Common HDR requires verified HDR10 in every clip; SDR is not relabeled as HDR")
    if any(color_signature(info) != color_signature(infos[0]) for info in infos[1:]):
        raise VideoToolError("Color characteristics or static HDR metadata differ; choose explicit SDR conversion if supported")


def _video_offset(info: MediaInfo) -> float:
    # FFmpeg subtracts the input start time, then setpts above subtracts any remaining video delay.
    try:
        start = float(info.metadata.get("format", {}).get("start_time", 0))
    except (ValueError, TypeError):
        start = 0
    return (info.start_time or 0) - start if isfinite(start) else 0


def _merge_normalized(
    infos: list[MediaInfo], output: Path, *, color: str, fps: Fraction | None,
    size: tuple[int, int], preset: str | None,
) -> Path:
    _normalize_color(infos, color)
    sdr = color == "sdr"
    check_aspect(preset, size)
    options = encode_options(infos[0], preset, sdr=sdr)
    if fps is None and any(_frame_rate(info) != _frame_rate(infos[0]) for info in infos[1:]):
        raise VideoToolError("Input frame rates differ; specify --fps to normalize frame rate")
    for info in infos:
        if len(info.video_streams) != 1 or any(
            stream.get("codec_type") not in {"video", "audio"} for stream in info.metadata.get("streams", [])
        ):
            raise VideoToolError(f"Normalization cannot preserve auxiliary streams in {info.path}; use compatible stream copy")

    width, height = size
    tracks = max(len(info.audio_streams) for info in infos)
    args: list[str] = []
    filters: list[str] = []
    concat_inputs = ""
    for index, info in enumerate(infos):
        args.extend(["-i", info.path])
        if not sdr and info.bit_depth != infos[0].bit_depth:
            raise VideoToolError(f"Normalization would change bit depth without explicit SDR conversion: {info.path}")
        conversion = sdr_filter(info) if sdr else ""
        transform = f"{conversion}," if conversion else ""
        filters.append(
            f"[{index}:V:0]settb=AVTB,setpts=PTS-STARTPTS,{transform}"
            f"scale={width}:{height}:flags=lanczos:force_original_aspect_ratio=decrease:"
            f"force_divisible_by=2:reset_sar=1,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2[v{index}]"
        )
        video_offset = _video_offset(info)
        filters.extend(audio_filters(index, len(info.audio_streams), tracks, info.duration, video_offset))
        concat_inputs += f"[v{index}]" + "".join(f"[a{index}_{track}]" for track in range(tracks))
    audio_outputs = [f"[outa{track}]" for track in range(tracks)]
    video_output = "[joined]" if fps else "[outv]"
    filters.append(f"{concat_inputs}concat=n={len(infos)}:v=1:a={tracks}{video_output}{''.join(audio_outputs)}")
    if fps:
        filters.append(f"[joined]fps={fps.numerator}/{fps.denominator}[outv]")
    args.extend(["-filter_complex", ";".join(filters), "-map", "[outv]", *options,
                 "-fps_mode:v", "passthrough", "-enc_time_base:v", "filter"])
    if tracks:
        for label in audio_outputs:
            args.extend(["-map", label])
        args.extend(["-c:a", "aac"])
    return render(
        args, output, infos, size=size, sdr=sdr, duration=sum(info.duration for info in infos),
        warnings=("Normalized audio uses stereo 48 kHz AAC; missing tracks are filled with silence.",) if tracks else (),
    )


def merge(
    inputs: list[Path], output: Path, *, normalize: bool = False, color: str = "reject",
    fps: str | float | None = None, size: tuple[int, int] | None = None, preset: str | None = None,
) -> Path:
    """Merge two or more inputs, stream-copying only verified compatible sources."""
    if len(inputs) < 2:
        raise VideoToolError("Merge requires at least two inputs")
    if color not in {"reject", "hdr", "sdr"}:
        raise VideoToolError(f"Unknown merge color policy: {color}")
    if not normalize and (color != "reject" or fps is not None or size is not None or preset is not None):
        raise VideoToolError("Color, FPS, size and preset options require normalization")
    rate = positive_rate(fps) if fps is not None else None
    if fps is not None and rate is None:
        raise VideoToolError("FPS must be a positive number or ratio")
    output, _ = output_paths(output)
    infos = [probe(path) for path in inputs]
    if not normalize:
        return _merge_copy(infos, output)
    if size is None:
        size = (infos[0].width, infos[0].height)
    if len(size) != 2 or any(type(value) is not int or value <= 0 or value % 2 for value in size):
        raise VideoToolError("Normalized size must contain two positive even dimensions")
    return _merge_normalized(infos, output, color=color, fps=rate, size=size, preset=preset)
