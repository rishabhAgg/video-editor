from ..exceptions import VideoToolError
from .metadata import mastering_signature


def color_signature(info):
    try:
        static = mastering_signature(info.mastering_display)
    except (KeyError, ValueError, ZeroDivisionError, TypeError) as exc:
        raise VideoToolError(f"Invalid mastering display metadata in {info.path}") from exc
    return (info.color_primaries, info.color_transfer, info.color_space, info.color_range,
            info.hdr_format, static, info.max_cll, info.max_fall, info.hdr10_plus, info.dolby_vision)


def validate_export(source, output, *, size=None, sdr=False, encoded=False):
    expected_size = size or (source.width, source.height)
    if (output.width, output.height) != expected_size:
        raise VideoToolError(f"Export dimensions are {output.width}×{output.height}; expected {expected_size}.")
    if sdr:
        if (output.hdr_format is not None or (output.color_primaries, output.color_transfer,
                output.color_space, output.color_range) != ("bt709", "bt709", "bt709", "tv")):
            raise VideoToolError("Export did not validate as Rec. 709 SDR.")
    else:
        if color_signature(output) != color_signature(source):
            raise VideoToolError("Export changed color signaling or detected HDR metadata; no output published.")
        if output.bit_depth != (10 if encoded and source.is_hdr else 8 if encoded else source.bit_depth):
            raise VideoToolError("Export bit depth does not match the requested profile.")
    if not encoded and not sdr and (output.video_codec, output.pixel_format) != (
        source.video_codec, source.pixel_format
    ):
        raise VideoToolError("Stream-copy output does not match the source video codec or pixel format.")
    if encoded and not sdr and source.is_hdr and (
        output.video_codec != "hevc" or output.profile != "Main 10" or output.pixel_format != "yuv420p10le"
    ):
        raise VideoToolError("HDR export did not validate as HEVC Main 10 with yuv420p10le.")
