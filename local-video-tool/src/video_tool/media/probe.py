import json
import logging
import math
from pathlib import Path
import re

from ..exceptions import VideoToolError
from ..models import MediaInfo
from ..utils.ffmpeg import capture
from .metadata import classify, mastering_signature


def _tag(value):
    return None if value in (None, "unknown", "unspecified", "reserved", "N/A") else value


def _number(value, cast):
    try:
        result = cast(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _pixel_properties(fmt, reported):
    depth = _number(reported, int)
    match = re.search(r"(?:p|gray)(9|10|12|14|16)(?:le|be)$", fmt)
    packed = re.match(r"p(?:0|2|4)(10|12|16)(?:le|be)$", fmt)
    if match or packed:
        physical_depth = int((match or packed)[1])
    elif fmt.startswith(("yuv", "gbrp", "nv12", "nv21")) or fmt in {"rgb24", "bgr24", "gray"}:
        physical_depth = 8
    else:
        physical_depth = 0
    depth = min(depth, physical_depth) if depth and physical_depth else physical_depth or depth or 0
    subsampling = next((f"4:{n[1]}:{n[2]}" for n in ("420", "422", "444", "440", "411", "410")
                        if n in fmt), None)
    if not subsampling:
        if fmt in {"nv12", "nv21"} or fmt.startswith("p0"):
            subsampling = "4:2:0"
        elif fmt.startswith("p2"):
            subsampling = "4:2:2"
        elif fmt.startswith(("gbr", "rgb", "bgr", "p4")):
            subsampling = "4:4:4"
    return depth, subsampling


def from_probe(path: Path, metadata: dict, frames: list[dict] | None = None) -> MediaInfo:
    streams = metadata.get("streams", [])
    videos = [s for s in streams if s.get("codec_type") == "video"]
    video = next((s for s in videos if not s.get("disposition", {}).get("attached_pic")), None)
    if video is None:
        raise VideoToolError(f"No video stream in {path}")
    duration = (_number(video.get("duration"), float)
                or _number(metadata.get("format", {}).get("duration"), float))
    if not duration or duration < 0 or not video.get("width") or not video.get("height"):
        raise VideoToolError(f"Missing usable video dimensions or duration in {path}")
    fmt = video.get("pix_fmt", "unknown")
    depth, subsampling = _pixel_properties(fmt, video.get("bits_per_raw_sample"))
    primaries, transfer, matrix, color_range = map(_tag, (video.get("color_primaries"),
        video.get("color_transfer"), video.get("color_space"), video.get("color_range")))
    side_data = [entry for item in [video, *(frames or [])] for entry in item.get("side_data_list", [])]
    types = [entry.get("side_data_type", "").lower() for entry in side_data]
    masters = [entry for entry, tag in zip(side_data, types) if "mastering display" in tag]
    lights = [entry for entry, tag in zip(side_data, types) if "content light" in tag]
    mastering = masters[0] if masters else None
    light = lights[0] if lights else {}
    hdr10_plus = any("hdr10+" in tag or "2094-40" in tag for tag in types)
    dolby_vision = any("dolby vision" in tag or "dovi" in tag for tag in types)
    hdr_format = classify(depth, primaries, transfer, matrix, hdr10_plus=hdr10_plus,
                          dolby_vision=dolby_vision, static_metadata=mastering is not None or bool(light))
    try:
        conflicting_static = len({mastering_signature(entry) for entry in masters}) > 1 or len({
            (entry.get("max_content"), entry.get("max_average")) for entry in lights
        }) > 1
    except (KeyError, ValueError, ZeroDivisionError, TypeError):
        conflicting_static = True
    conflicting_color = any(
        _tag(frame.get(key)) not in (None, _tag(video.get(key)))
        for frame in (frames or []) for key in ("color_primaries", "color_transfer", "color_space", "color_range")
    )
    if conflicting_static or conflicting_color:
        hdr_format = "UNKNOWN"
    warnings = ["Unknown or contradictory color metadata; HDR status is unverified."] if hdr_format == "UNKNOWN" else []
    if hdr_format == "HDR10":
        if mastering is None:
            warnings.append("PQ HDR10 signal has no detected mastering display metadata.")
        if not light:
            warnings.append("PQ HDR10 signal has no detected content-light metadata.")
    if hdr10_plus or dolby_vision:
        warnings.append("Dynamic HDR signaling was detected, but per-frame payload preservation is not verified.")
    return MediaInfo(
        path=str(path), duration=duration, width=video["width"], height=video["height"],
        fps=video.get("r_frame_rate", "0/0"), average_fps=video.get("avg_frame_rate", "0/0"),
        video_codec=video.get("codec_name", "unknown"), profile=video.get("profile"),
        pixel_format=fmt, bit_depth=depth, chroma_subsampling=subsampling,
        time_base=video.get("time_base", "0/0"), start_time=_number(video.get("start_time"), float),
        frame_count=_number(video.get("nb_frames"), int), color_primaries=primaries,
        color_transfer=transfer, color_space=matrix, color_range=color_range,
        is_hdr=hdr_format not in (None, "UNKNOWN"), hdr_format=hdr_format,
        mastering_display=mastering, max_cll=_number(light.get("max_content"), int),
        max_fall=_number(light.get("max_average"), int), hdr10_plus=hdr10_plus,
        dolby_vision=dolby_vision, audio_streams=[s for s in streams if s.get("codec_type") == "audio"],
        video_streams=[video, *(s for s in videos if s is not video)],
        metadata={**metadata, "sampled_frames": frames or []}, warnings=warnings,
    )


def probe(path: Path) -> MediaInfo:
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise VideoToolError(f"Input file not found: {path}")
    metadata = json.loads(capture("ffprobe", ["-show_format", "-show_streams", "-show_data_hash", "sha256",
                                            "-of", "json", str(path)]))
    info = from_probe(path, metadata)
    index = str(info.video_streams[0]["index"])
    frames = json.loads(capture("ffprobe", ["-select_streams", index, "-read_intervals", "%+#32",
        "-show_frames", "-show_entries",
        "frame=best_effort_timestamp_time,color_primaries,color_transfer,color_space,color_range:frame_side_data",
        "-of", "json", str(path)])).get("frames", [])
    info = from_probe(path, metadata, frames)
    for warning in info.warnings:
        logging.warning("%s: %s", path.name, warning)
    return info
