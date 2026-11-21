import shutil
import subprocess

import pytest

from video_tool.crop.preview import PreviewCancelled, build_proxy


pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="FFmpeg required")


def test_proxy_preserves_variable_frame_timestamps_and_seeks(tmp_path):
    source = tmp_path / "vfr.mp4"
    target = tmp_path / "proxy.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=red:size=160x90:rate=25:duration=0.16",
        "-vf", "drawbox=color=lime:t=fill:enable='eq(n,1)',drawbox=color=blue:t=fill:enable='eq(n,2)',"
        "settb=1/1000,setpts='5000+if(eq(N,0),0,if(eq(N,1),40,if(eq(N,2),200,240)))'",
        "-fps_mode", "passthrough", "-c:v", "libx264", "-g", "1", str(source),
    ], check=True)
    proxy = build_proxy(source, target, max_side=96, cache_size=2)

    assert proxy.timestamps == pytest.approx((0.0, 0.04, 0.20, 0.24))
    assert (proxy.width, proxy.height) == (96, 54)
    assert proxy.frame_index(0.199) == 1
    assert proxy.frame_index(10.0) == 3
    lime = proxy.frame(1)
    assert lime.rgb[1] > 200
    blue = proxy.frame(2)
    assert blue.rgb[2] > 200
    assert proxy.frame(1) is lime
    assert proxy.frame(3).rgb[0] > 200
    assert proxy.frame(2) is not blue


def test_cancelled_proxy_does_not_leave_output_or_remove_existing_file(tmp_path):
    target = tmp_path / "proxy.mp4"
    with pytest.raises(PreviewCancelled):
        build_proxy(tmp_path / "unused", target, cancel=lambda: True)
    assert not target.exists()
    target.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        build_proxy(tmp_path / "unused", target)
    assert target.read_bytes() == b"existing"
