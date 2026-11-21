from dataclasses import asdict
from fractions import Fraction
import json
import logging
import os
from pathlib import Path
import tempfile

from ..config import read_config
from ..exceptions import VideoToolError
from ..utils.ffmpeg import capture, encode
from ..utils.paths import output_paths
from .metadata import x265_mastering
from .probe import probe
from .validation import validate_export


def encode_options(info, preset=None, *, sdr=False):
    use_hdr = info.is_hdr and not sdr
    if info.hdr_format == "UNKNOWN" or (info.is_hdr and info.hdr_format != "HDR10"):
        raise VideoToolError(f"Re-encoding {info.hdr_format} is not supported; use stream copy where available.")
    if not (use_hdr or sdr) and info.bit_depth != 8:
        raise VideoToolError("The SDR preset does not implicitly reduce source bit depth.")
    if not (use_hdr or sdr) and not info.color_range:
        raise VideoToolError("Re-encoding requires a known source color range.")
    preset = preset or ("hdr10_hevc" if use_hdr else "sdr_h264")
    settings = read_config("export_presets.json").get(preset)
    if settings is None or settings.get("hdr") is not use_hdr:
        raise VideoToolError(f"Preset {preset!r} does not match the requested color pipeline.")
    codec = "libx265" if use_hdr else "libx264"
    if settings.get("codec") != codec:
        raise VideoToolError(f"Preset {preset!r} must use {codec}.")
    args = ["-c:v", codec, "-crf", str(settings["crf"]), "-preset", settings["speed"],
            "-pix_fmt", "yuv420p10le" if use_hdr else "yuv420p"]
    if use_hdr:
        if info.bit_depth != 10 or info.color_range not in ("tv", "pc"):
            raise VideoToolError("HDR10 encoding requires 10-bit input with a known video range.")
        params = ["hdr10=1", "hdr10-opt=1"]
        try:
            mastering = x265_mastering(info.mastering_display)
        except (KeyError, ValueError, ZeroDivisionError, TypeError) as exc:
            raise VideoToolError(f"Cannot preserve mastering metadata: {exc}") from exc
        if mastering:
            params.append(f"master-display={mastering}")
        if info.max_cll is not None or info.max_fall is not None:
            if not all(isinstance(v, int) and 0 <= v <= 65535 for v in (info.max_cll, info.max_fall)):
                raise VideoToolError("Cannot preserve incomplete or invalid content-light metadata.")
            params.append(f"max-cll={info.max_cll},{info.max_fall}")
        else:
            # hdr10=1 enables x265's default 0,0 CLL SEI unless explicitly disabled.
            params.append("cll=0")
        args += ["-profile:v", "main10", "-x265-params", ":".join(params)]
    primaries, transfer, matrix, color_range = (
        ("bt709", "bt709", "bt709", "tv") if sdr else
        (info.color_primaries, info.color_transfer, info.color_space, info.color_range)
    )
    for option, value in zip(("-color_primaries", "-color_trc", "-colorspace", "-color_range"),
                             (primaries, transfer, matrix, color_range)):
        if value:
            args += [option, value]
    return args


def sdr_filter(info):
    if (info.color_primaries, info.color_transfer, info.color_space, info.color_range) == (
        "bt709", "bt709", "bt709", "tv"
    ) and info.hdr_format is None:
        return "format=yuv420p"
    if info.hdr_format not in (None, "HDR10") or not info.color_range:
        raise VideoToolError(f"Cannot convert {info.hdr_format or 'SDR'} with unverified or unsupported color.")
    remove_hdr = (",sidedata=delete:type=MASTERING_DISPLAY_METADATA,"
                  "sidedata=delete:type=CONTENT_LIGHT_LEVEL")
    if " zscale " in capture("ffmpeg", ["-filters"]):
        if info.hdr_format == "HDR10":
            return ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
                    "tonemap=mobius,zscale=t=bt709:m=bt709:r=limited,format=yuv420p" + remove_hdr)
        return "zscale=p=bt709:t=bt709:m=bt709:r=limited,format=yuv420p"
    scale_options = capture("ffmpeg", ["-h", "filter=scale"])
    if "perceptual tone mapping" in scale_options and "out_transfer" in scale_options:
        return ("scale=out_primaries=bt709:out_transfer=bt709:out_color_matrix=bt709:"
                "out_range=tv:intent=perceptual,format=yuv420p" + remove_hdr)
    raise VideoToolError("Explicit color conversion requires perceptual swscale or FFmpeg with libzimg (zscale).")


def check_aspect(preset, size):
    if preset is None:
        return
    profile = read_config("export_presets.json").get(preset, {})
    if ratio := profile.get("aspect"):
        if size[0] * ratio[1] != size[1] * ratio[0]:
            raise VideoToolError(f"Preset {preset} requires {ratio[0]}:{ratio[1]} output.")


def render(args, output, sources, *, expected=None, size=None, sdr=False,
           progress=None, cancel=None, duration=None, warnings=()):
    output, report = output_paths(Path(output))
    output.parent.mkdir(parents=True, exist_ok=True)
    reference = expected or sources[0]
    encoded = "-c:v" in args and args[args.index("-c:v") + 1] != "copy"
    with tempfile.TemporaryDirectory(prefix=".video-tool-", dir=output.parent) as work:
        staged = Path(work) / f"video{output.suffix}"
        ffmpeg_args = list(args)
        if output.suffix.lower() in {".mp4", ".mov", ".m4v"} and "libx265" in args:
            ffmpeg_args += ["-tag:v", "hvc1", "-movflags", "+faststart"]
        mux_warnings = encode(ffmpeg_args, staged, duration or sum(s.duration for s in sources), progress, cancel)
        info = probe(staged)
        validate_export(reference, info, size=size, sdr=sdr, encoded=encoded)
        if cancel and cancel():
            raise VideoToolError("Export canceled")
        messages = list(dict.fromkeys([*warnings, *mux_warnings, *info.warnings]))
        for message in (*warnings, *mux_warnings):
            logging.warning("%s", message)
        if info.is_hdr:
            messages.append("Verification checks the encoded pixel format and detected color/side-data signaling, "
                            "not the color grade or whether a platform will retain HDR after upload.")
        info.path = str(output)
        info.metadata["format"]["filename"] = str(output)
        staged_report = Path(work) / "report.json"
        staged_report.write_text(json.dumps({"sources": [asdict(s) for s in sources],
            "output": asdict(info), "validation": "passed", "warnings": messages}, indent=2) + "\n",
            encoding="utf-8")
        try:
            os.link(staged_report, report)
        except FileExistsError as exc:
            raise VideoToolError(f"Refusing to overwrite {report}") from exc
        try:
            os.link(staged, output)
        except OSError as exc:
            report.unlink()
            if isinstance(exc, FileExistsError):
                raise VideoToolError(f"Refusing to overwrite {output}") from exc
            raise
    if progress:
        progress(1.0)
    return output


def export_crop(project, output, preset=None, progress=None, cancel=None):
    from .crop import filtergraph

    info = probe(Path(project.source))
    project.validate(info.duration)
    video = info.video_streams[0]
    if video.get("sample_aspect_ratio") not in (None, "N/A", "0:1", "1:1"):
        raise VideoToolError("Crop requires square-pixel source video.")
    if any(item.get("rotation") for item in video.get("side_data_list", [])):
        raise VideoToolError("Crop requires a source without a rotation transform.")
    size = (project.output_width, project.output_height)
    check_aspect(preset, size)
    fmt = "yuv420p10le" if info.is_hdr else "yuv420p"
    options = encode_options(info, preset)
    graph = filtergraph(project, info.width, info.height, fmt).replace("[0:v:0]", f"[0:{video['index']}]")
    if info.audio_streams and info.start_time is not None:
        container_start = info.metadata["format"].get("start_time", "0")
        if container_start != "N/A":
            offset = Fraction(str(info.start_time)) - Fraction(container_start)
            if offset > 0:
                # Keep crop keyframes relative to video while retaining the original A/V offset.
                graph += f";[v]setpts=PTS+({offset.numerator}/{offset.denominator})/TB[synced]"
    label = "[synced]" if graph.endswith("[synced]") else "[v]"
    args = ["-filter_complex_threads", "1", "-i", info.path, "-filter_complex", graph,
            "-map", label, "-map", "0:a?", "-map_metadata", "0", "-fps_mode", "passthrough",
            "-enc_time_base:v", info.time_base, *options, "-c:a", "aac", "-b:a", "320k"]
    return render(args, Path(output), [info], size=size, progress=progress, cancel=cancel)
