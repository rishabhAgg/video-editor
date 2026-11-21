import json

import pytest

from video_tool.crop.keyframes import Keyframe, Project
from video_tool.exceptions import VideoToolError
from video_tool.media.encode import export_crop
from video_tool.media.probe import probe


@pytest.mark.parametrize("mode", ["dynamic_crop", "blurred_background", "dynamic_crop_blurred_background"])
def test_hdr_crop_roundtrip(tmp_path, pq_source, mode):
    output = tmp_path / "portrait.mp4"
    project = Project(source=str(pq_source), output_width=90, output_height=160, mode=mode,
                      keyframes=[Keyframe(0, 0.2), Keyframe(0.75, 0.8)])
    assert export_crop(project, output, "instagram_hdr") == output
    report = json.loads(output.with_name("portrait.mp4.report.json").read_text())
    info = report["output"]
    assert (info["width"], info["height"], info["profile"], info["pixel_format"]) == (90, 160, "Main 10", "yuv420p10le")
    assert (info["max_cll"], info["max_fall"], info["hdr_format"]) == (773, 210, "HDR10")
    assert info["mastering_display"] == report["sources"][0]["mastering_display"]


def test_refuses_source_overwrite_and_cleans_canceled_export(tmp_path, pq_source):
    original = pq_source.read_bytes()
    project = Project(source=str(pq_source), output_width=90, output_height=160)
    with pytest.raises(VideoToolError, match="overwrite"):
        export_crop(project, pq_source)
    assert pq_source.read_bytes() == original
    output = tmp_path / "canceled.mp4"
    with pytest.raises(VideoToolError, match="canceled"):
        export_crop(project, output, cancel=lambda: True)
    assert not list(tmp_path.glob("canceled*")) and not list(tmp_path.glob(".video-tool-*"))


def test_crop_does_not_fabricate_missing_static_hdr_metadata(tmp_path, ffmpeg):
    source = tmp_path / "pq-without-sei.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=160x90:r=8:d=0.5,format=yuv420p10le,"
           "setparams=color_primaries=bt2020:color_trc=smpte2084:colorspace=bt2020nc:range=limited",
           "-c:v", "libx265", "-preset", "ultrafast", source)
    output = export_crop(Project(str(source), 90, 160), tmp_path / "portrait.mp4")
    info = probe(output)
    assert info.hdr_format == "HDR10"
    assert info.mastering_display is info.max_cll is info.max_fall is None


def test_crop_keeps_variable_frame_timestamps_and_delayed_video(tmp_path, ffmpeg):
    source = tmp_path / "delayed.mp4"
    ffmpeg("-itsoffset", "0.25", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=12:d=1,"
           "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=limited",
           "-f", "lavfi", "-i", "sine=d=1.3", "-vf", "select='not(eq(n,3)+eq(n,6))'",
           "-fps_mode", "passthrough", "-c:v", "libx264", "-c:a", "aac", source)
    output = export_crop(Project(str(source), 90, 160), tmp_path / "portrait.mp4")

    def timeline(path):
        frames = probe(path).metadata["sampled_frames"]
        return [float(frame["best_effort_timestamp_time"]) for frame in frames]

    input_times = timeline(source)
    assert input_times[0] == pytest.approx(0.25)
    assert timeline(output) == pytest.approx(input_times, abs=1e-3)
