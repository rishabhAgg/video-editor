import json
import shutil
import struct
import subprocess

import pytest

from video_tool.crop.interpolation import position
from video_tool.crop.keyframes import MODES, Keyframe, Project
from video_tool.media.crop import filtergraph


@pytest.mark.parametrize("method,expected", [
    ("linear", (0.25, 0.5)), ("smooth", (0.15625, 0.453125)), ("hold", (0, 0.375)),
])
def test_interpolation_methods_and_endpoint_clamping(method, expected):
    keys = [Keyframe(1, 0, 0.375), Keyframe(5, 1, 0.875), Keyframe(7, 0, 1)]
    assert position(keys, 0, method) == (0, 0.375)
    assert position(keys, 2, method) == expected
    assert position(keys, 5, method) == (1, 0.875)
    assert position(keys, 8, method) == (0, 1)
    assert position([], 2, method) == (0.5, 0.5)


def test_project_loads_relative_source_and_roundtrips_versioned_json(tmp_path):
    path = tmp_path / "crop.json"
    path.write_text(json.dumps({
        "version": 1, "source": "input.mp4", "output_aspect_ratio": "9:16",
        "keyframes": [{"time": 0, "center_x": 0.75}],
    }), encoding="utf-8")
    project = Project.load(path)
    assert project.source == str(tmp_path / "input.mp4")
    assert project.keyframes == [Keyframe(0, 0.75, 0.5)]
    project.source_metadata = {"hdr": "HDR10"}
    project.inset = 0.75
    project.dark_overlay = 0.2
    project.save(path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert (saved["version"], saved["output_aspect_ratio"], saved["source"]) == (1, "9:16", "input.mp4")
    assert Project.load(path) == project


@pytest.mark.parametrize("options,duration", [
    ({"output_width": 719}, None),
    ({"keyframes": [Keyframe(1, 0), Keyframe(1, 1)]}, None),
    ({"keyframes": [Keyframe(2, 0)]}, 1),
    ({"keyframes": [Keyframe(0, 0.5, float("nan"))]}, None),
    ({"inset": 0}, None),
])
def test_project_validation(options, duration):
    with pytest.raises(ValueError):
        Project("input.mp4", **options).validate(duration)


def _render(project, depth, duration=1):
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg required")
    pixel_format = f"yuv420p{depth}le"
    source = (
        f"nullsrc=s=144x96:r=1:d={duration},format={pixel_format},"
        f"geq=lum='X+64':cb={2 ** (depth - 1)}:cr={2 ** (depth - 1)},setpts=PTS+7/TB"
    )
    result = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", source,
        "-filter_complex", filtergraph(project, 144, 96, pixel_format), "-map", "[v]",
        "-pix_fmt", pixel_format, "-f", "rawvideo", "-",
    ], capture_output=True, check=True)
    return result.stdout


def test_ffmpeg_hold_uses_elapsed_pts_not_absolute_time():
    project = Project("input.mp4", 48, 96, interpolation="hold", keyframes=[Keyframe(0, 0), Keyframe(1.5, 1)])
    samples = _render(project, 10, duration=3)
    frame_size = 48 * 96 * 3
    assert len(samples) == frame_size * 3
    assert [struct.unpack_from("<H", samples, i * frame_size)[0] for i in range(3)] == [64, 64, 160]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("depth", [10, 12])
def test_ffmpeg_preserves_sub_8bit_precision_and_canvas(mode, depth):
    project = Project("input.mp4", 48, 96, mode, dark_overlay=0.2)
    samples = _render(project, depth)
    assert len(samples) == 48 * 96 * 3
    row = struct.unpack_from("<48H", samples, 48 * 48 * 2)
    assert len({value % (1 << (depth - 8)) for value in row[8:-8]}) > 1
