from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.fixture
def ffmpeg():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and FFprobe required")

    def run(*args):
        result = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", *map(str, args)],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

    return run


@pytest.fixture
def pq_source(tmp_path: Path, ffmpeg):
    source = tmp_path / "gameplay.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=320x180:r=12:d=1,format=yuv420p10le,"
           "setparams=color_primaries=bt2020:color_trc=smpte2084:colorspace=bt2020nc:range=limited",
           "-c:v", "libx265", "-preset", "ultrafast",
           "-pix_fmt", "yuv420p10le", "-color_primaries", "bt2020", "-color_trc", "smpte2084",
           "-colorspace", "bt2020nc", "-color_range", "tv", "-x265-params",
           "hdr10=1:master-display=G(8500,39850)B(6550,2300)R(35400,14600)WP(15635,16450)L(10000000,50):max-cll=773,210",
           source)
    return source
