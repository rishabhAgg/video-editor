"""Low-resolution framing proxy; never used as an export input."""

import bisect
import json
import subprocess
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


class PreviewCancelled(Exception):
    pass


def _run(args: list[str], cancel: Callable[[], bool]) -> bytes:
    if cancel():
        raise PreviewCancelled
    with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        while True:
            try:
                stdout, stderr = process.communicate(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                if cancel():
                    process.kill()
                    process.communicate()
                    raise PreviewCancelled from None
        if cancel():
            raise PreviewCancelled
        if process.returncode:
            raise RuntimeError(stderr.decode(errors="replace").strip() or f"{args[0]} failed")
        return stdout


@dataclass(frozen=True)
class PreviewFrame:
    time: float
    width: int
    height: int
    rgb: bytes


class Proxy:
    """Timestamp index and bounded RGB cache, decoded by a single worker."""

    def __init__(self, path: Path, width: int, height: int, timestamps: tuple[float, ...], cache_size: int):
        self.path = path
        self.width = width
        self.height = height
        self.timestamps = timestamps
        self._cache_size = cache_size
        self._cache: OrderedDict[int, PreviewFrame] = OrderedDict()

    def frame_index(self, time: float) -> int:
        return max(0, bisect.bisect_right(self.timestamps, time) - 1)

    def frame(self, index: int, cancel: Callable[[], bool] = lambda: False) -> PreviewFrame:
        if not 0 <= index < len(self.timestamps):
            raise IndexError(index)
        if index in self._cache:
            self._cache.move_to_end(index)
            return self._cache[index]
        time = self.timestamps[index]
        # Seek just before the indexed frame to avoid rounding past its PTS.
        seek = (time + self.timestamps[index - 1]) / 2 if index else 0.0
        count = min(8, self._cache_size, len(self.timestamps) - index)
        rgb = _run([
            "ffmpeg", "-v", "error", "-nostdin", "-ss", f"{seek:.9f}",
            "-protocol_whitelist", "file,pipe", "-i", str(self.path),
            "-frames:v", str(count), "-an", "-fps_mode", "passthrough",
            "-c:v", "rawvideo", "-threads:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1",
        ], cancel)
        size = self.width * self.height * 3
        if len(rgb) != size * count:
            raise RuntimeError(f"Could not decode proxy frame at {time:.6f}s")
        for i in range(count):
            key = index + i
            self._cache[key] = PreviewFrame(self.timestamps[key], self.width, self.height, rgb[i * size:(i + 1) * size])
            self._cache.move_to_end(key)
            if len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return self._cache[index]


def build_proxy(source: Path, target: Path, cancel: Callable[[], bool] = lambda: False, *,
                max_side: int = 640, cache_size: int = 24) -> Proxy:
    if max_side < 2 or cache_size < 1:
        raise ValueError("Invalid proxy size or cache capacity")
    max_side -= max_side % 2
    created = False
    try:
        # Reserve a fresh path; a failed preview must never delete an existing file.
        with target.open("xb"):
            created = True
        _run([
            "ffmpeg", "-v", "error", "-nostdin", "-y", "-noautorotate",
            "-protocol_whitelist", "file,pipe", "-i", str(source),
            "-map", "0:V:0", "-an", "-sn", "-dn",
            "-vf", f"scale='min({max_side},iw)':'min({max_side},ih)':force_original_aspect_ratio=decrease:"
            "force_divisible_by=2,setsar=1,setpts=PTS-STARTPTS",
            "-fps_mode", "passthrough", "-enc_time_base", "1:1000000", "-video_track_timescale", "1000000",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "24", "-g", "1", "-pix_fmt", "yuv420p",
            "-f", "mp4", str(target),
        ], cancel)
        data = json.loads(_run([
            "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
            "-select_streams", "v:0", "-show_frames", "-show_streams",
            "-show_entries", "stream=width,height,time_base:frame=best_effort_timestamp", "-of", "json", str(target),
        ], cancel))
        stream, = data["streams"]
        numerator, denominator = map(int, stream["time_base"].split("/"))
        timestamps = tuple(int(frame["best_effort_timestamp"]) * numerator / denominator for frame in data["frames"])
        if not timestamps or timestamps[0] != 0 or any(b <= a for a, b in zip(timestamps, timestamps[1:])):
            raise RuntimeError("Proxy timestamps are missing or not increasing from zero")
        return Proxy(target, stream["width"], stream["height"], timestamps, cache_size)
    except BaseException:
        if created:
            target.unlink(missing_ok=True)
        raise
