import json
import shutil
import subprocess

import pytest

from video_tool.exceptions import VideoToolError
from video_tool.media.probe import probe
from video_tool.media.split import split

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="FFmpeg and FFprobe are required"
)


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", *map(str, args)], check=True)


def _manifest(path, *entries):
    path.write_text(json.dumps({"segments": entries}), encoding="utf-8")
    return path


def _segment(name, start, end):
    return {"name": name, "start": start, "end": end}


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "source.mkv"
    captions = tmp_path / "captions.srt"
    captions.write_text("1\n00:00:00,650 --> 00:00:00,850\nCaption\n", encoding="utf-8")
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=s=64x48:r=10:d=1.2",
        "-f", "lavfi", "-i", "sine=sample_rate=48000:duration=2",
        "-i", captions, "-map", "0:v", "-map", "1:a", "-map", "2:s",
        "-vf", "setpts=if(lt(N\\,3)\\,N\\,N*2),"
        "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
        "-fps_mode", "passthrough", "-c:v", "libx264", "-g", "4",
        "-c:a", "pcm_s16le", "-c:s", "srt", path,
    )
    return path


def test_fast_split_multiple_keeps_audio_and_subtitles(source, tmp_path):
    manifest = _manifest(
        tmp_path / "segments.json", _segment("intro", "00:00:00.000", "00:00:01.000"),
        _segment("end", "00:00:01.000", "00:00:01.800"),
    )
    with pytest.warns(UserWarning, match="keyframes"):
        outputs = split(source, manifest, tmp_path / "output")
    assert [output.name for output in outputs] == ["intro.mkv", "end.mkv"]
    first = probe(outputs[0])
    assert first.video_codec == "h264"
    assert [stream["codec_name"] for stream in first.audio_streams] == ["pcm_s16le"]
    assert any(stream["codec_type"] == "subtitle" for stream in first.metadata["streams"])


def test_accurate_split_uses_vfr_presentation_timestamps(source, tmp_path):
    manifest = _manifest(tmp_path / "segments.json", _segment("middle", "00:00:00:03", 5))
    (output,) = split(source, manifest, tmp_path / "output", mode="accurate")
    timestamps = subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "V:0", "-show_frames", "-show_entries",
        "frame=best_effort_timestamp_time", "-of", "json", str(output),
    ], text=True)
    pts = [float(frame["best_effort_timestamp_time"]) for frame in json.loads(timestamps)["frames"]]
    assert len(pts) == 2  # Display frames 3 and 4 are at 0.6 s and 0.8 s, not 0.3 s and 0.4 s.
    assert pts[1] - pts[0] == pytest.approx(0.2)
    result = probe(output)
    assert result.audio_streams[0]["codec_name"] == "aac"
    assert any(stream["codec_type"] == "subtitle" for stream in result.metadata["streams"])


def test_accurate_split_keeps_hdr10_static_metadata(pq_source, tmp_path):
    manifest = _manifest(tmp_path / "segments.json", _segment("hdr", 2, 8))
    (output,) = split(pq_source, manifest, tmp_path / "output", mode="accurate")
    result = probe(output)
    original = probe(pq_source)
    assert (result.hdr_format, result.bit_depth) == ("HDR10", 10)
    assert (result.max_cll, result.max_fall) == (original.max_cll, original.max_fall)
    assert result.mastering_display == original.mastering_display


@pytest.mark.parametrize("bad", [
    _segment("reverse", "00:00:01.000", "00:00:00.000"),
    _segment("past-end", "00:00:00.000", "00:00:19.000"),
    _segment("../escape", "00:00:00.000", "00:00:01.000"),
    _segment("missing-frame", 1, 100),
])
def test_split_validates_the_entire_manifest_before_writing(source, tmp_path, bad):
    manifest = _manifest(tmp_path / "segments.json", _segment("good", 0, 2), bad)
    with pytest.raises(VideoToolError):
        split(source, manifest, tmp_path / "output")
    assert not (tmp_path / "output").exists()
