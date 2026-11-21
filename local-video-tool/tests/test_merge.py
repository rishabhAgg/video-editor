import shutil
import subprocess
from fractions import Fraction

import pytest

from video_tool.exceptions import VideoToolError
from video_tool.media.merge import merge
from video_tool.media.probe import probe
from video_tool.utils.ffmpeg import capture

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="FFmpeg and FFprobe are required"
)


def _clip(path, *, size="64x48", fps=10, audio=True, max_cll=None):
    args = ["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i", f"testsrc2=s={size}:r={fps}:d=1"]
    if audio:
        args += ["-f", "lavfi", "-i", "sine=sample_rate=44100:duration=1", "-c:a", "aac"]
    if max_cll is None:
        args += ["-c:v", "libx264", "-vf", "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709"]
    else:
        args += ["-c:v", "libx265", "-preset", "ultrafast", "-vf", "format=yuv420p10le,"
                 "setparams=range=limited:color_primaries=bt2020:color_trc=smpte2084:colorspace=bt2020nc",
                 "-tag:v", "hvc1", "-x265-params", f"log-level=error:pools=1:frame-threads=1:max-cll={max_cll},200"]
    subprocess.run([*args, str(path)], check=True)
    return path


def test_merge_copies_compatible_streams_and_escapes_paths(tmp_path):
    first = _clip(tmp_path / "part 1.mp4")
    second = _clip(tmp_path / "part '2'.mp4")
    original = probe(first)
    output = merge([first, second], tmp_path / "out.mp4")
    result = probe(output)
    assert result.duration == pytest.approx(2, abs=0.1)
    assert result.frame_count == 20
    assert result.video_streams[0]["extradata_hash"] == original.video_streams[0]["extradata_hash"]
    assert result.audio_streams[0]["sample_rate"] == "44100"
    assert not list(tmp_path.glob("*.ffconcat"))
    with pytest.raises(VideoToolError, match="overwrite"):
        merge([first, second], first)


def test_normalize_requires_explicit_fps_and_fills_missing_audio(tmp_path):
    first = _clip(tmp_path / "one.mp4")
    second = _clip(tmp_path / "two.mp4", size="80x64", fps=12, audio=False)
    dest = tmp_path / "out.mp4"
    with pytest.raises(VideoToolError, match="Incompatible input"):
        merge([first, second], dest)
    with pytest.raises(VideoToolError, match="specify --fps"):
        merge([first, second], dest, normalize=True)
    result = probe(merge([first, second], dest, normalize=True, fps=12, size=(96, 64)))
    assert Fraction(result.average_fps) == 12
    assert (result.width, result.height) == (96, 64)
    assert result.duration == pytest.approx(2, abs=0.1)
    assert len(result.audio_streams) == 1
    assert result.audio_streams[0]["sample_rate"] == "48000"


def test_explicit_sdr_keeps_limited_rec709_and_cannot_relabel_it_hdr(tmp_path):
    first = _clip(tmp_path / "one.mp4", audio=False)
    second = _clip(tmp_path / "two.mp4", audio=False)
    dest = tmp_path / "out.mp4"
    with pytest.raises(VideoToolError, match="SDR is not relabeled as HDR"):
        merge([first, second], dest, normalize=True, color="hdr")
    result = probe(merge([first, second], dest, normalize=True, color="sdr"))
    assert result.hdr_format is None
    assert (result.color_primaries, result.color_transfer, result.color_space, result.color_range) == (
        "bt709", "bt709", "bt709", "tv",
    )


def test_mixed_hdr_and_sdr_merge_requires_explicit_sdr_conversion(pq_source, tmp_path):
    if " zscale " not in capture("ffmpeg", ["-filters"]):
        scale = capture("ffmpeg", ["-h", "filter=scale"])
        if not ("perceptual tone mapping" in scale and "out_transfer" in scale):
            pytest.skip("FFmpeg has neither zscale nor perceptual swscale")

    sdr_source = _clip(tmp_path / "sdr.mp4", size="320x180", fps=12, audio=False)
    inputs = [pq_source, sdr_source]
    output = tmp_path / "mixed.mp4"
    assert probe(pq_source).hdr_format == "HDR10"
    assert probe(sdr_source).hdr_format is None
    with pytest.raises(VideoToolError, match="choose explicit SDR conversion"):
        merge(inputs, output, normalize=True)
    assert not output.exists()

    result = probe(merge(inputs, output, normalize=True, color="sdr"))
    assert result.frame_count == 24
    assert (result.hdr_format, result.bit_depth) == (None, 8)
    assert (result.color_primaries, result.color_transfer, result.color_space, result.color_range) == (
        "bt709", "bt709", "bt709", "tv",
    )
    assert result.mastering_display is result.max_cll is result.max_fall is None


def test_hdr_merge_rejects_mismatched_metadata_and_preserves_match(tmp_path):
    first = _clip(tmp_path / "first-hdr.mp4", audio=False, max_cll=1000)
    same = _clip(tmp_path / "same-hdr.mp4", size="80x64", audio=False, max_cll=1000)
    different = _clip(tmp_path / "other-hdr.mp4", audio=False, max_cll=800)
    output = tmp_path / "out.mp4"
    with pytest.raises(VideoToolError, match="static HDR metadata"):
        merge([first, different], output, normalize=True)
    result = probe(merge([first, same], output, normalize=True))
    assert (result.hdr_format, result.bit_depth, result.max_cll, result.max_fall) == ("HDR10", 10, 1000, 200)
    assert result.frame_count == 20
