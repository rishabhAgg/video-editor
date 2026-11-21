"""Parse elapsed time and non-drop-frame labels without rounding frame timestamps."""

import re
import subprocess
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Iterable

from ..exceptions import VideoToolError

_CLOCK = re.compile(r"([0-9]{2,}):([0-5][0-9]):([0-5][0-9])(?:\.([0-9]{1,6}))?")
_FRAME = re.compile(r"([0-9]{2,}):([0-5][0-9]):([0-5][0-9]):([0-9]{2,})")


def positive_rate(value: object) -> Fraction | None:
    try:
        rate = Fraction(str(value))
    except (ValueError, ZeroDivisionError):
        return None
    return rate if rate > 0 else None


def parse_timecode(value: object, fps: Fraction | None = None) -> Decimal | int:
    """Return elapsed seconds (Decimal) or a zero-based display frame index (int)."""
    if isinstance(value, str):
        clock = _CLOCK.fullmatch(value)
        if clock:
            hours, minutes, seconds, fraction = clock.groups()
            whole = int(hours) * 3600 + int(minutes) * 60 + int(seconds)
            return Decimal(f"{whole}.{fraction or '0'}")
        frame = _FRAME.fullmatch(value)
        if frame and fps and 0 < fps < float("inf"):
            hours, minutes, seconds, number = map(int, frame.groups())
            nominal = max(1, round(fps))
            if number >= nominal:
                raise VideoToolError(f"Frame field must be less than {nominal}: {value!r}")
            return (hours * 3600 + minutes * 60 + seconds) * nominal + number
    if type(value) is int and value >= 0 and fps and 0 < fps < float("inf"):
        return value
    raise VideoToolError(
        f"Invalid timecode {value!r}; use HH:MM:SS.mmm or, with known FPS, "
        "HH:MM:SS:FF (non-drop-frame) or a zero-based frame index"
    )


def frame_timestamps(
    source: Path, indices: Iterable[int], *, start_time: Decimal
) -> dict[int, Decimal]:
    """Resolve display-frame indices to elapsed best-effort presentation timestamps."""
    remaining = set(indices)
    if not remaining:
        return {}
    found = {}
    command = [
        "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
        "-select_streams", "V:0", "-show_frames",
        "-show_entries", "frame=best_effort_timestamp_time", "-of", "compact=p=1:nk=0",
        str(source),
    ]
    try:
        with subprocess.Popen(command, stdout=subprocess.PIPE, text=True) as process:
            assert process.stdout is not None
            index = 0
            try:
                for line in process.stdout:
                    if not line.startswith(("frame|", "frame\n")):
                        continue
                    if index in remaining:
                        match = re.search(r"\|best_effort_timestamp_time=(-?[0-9]+(?:\.[0-9]+)?)(?:\||\s|$)", line)
                        if not match:
                            raise VideoToolError(f"Frame {index} has no usable timestamp in {source}")
                        found[index] = Decimal(match[1]) - start_time
                        remaining.remove(index)
                        if not remaining:
                            break
                    index += 1
            finally:
                if process.poll() is None:
                    process.terminate()
    except OSError as exc:
        raise VideoToolError(f"Could not read frame timestamps from {source}: {exc}") from exc
    if remaining:
        raise VideoToolError(f"Frame {min(remaining)} is outside the source: {source}")
    return found


def ffmpeg_time(seconds: Decimal) -> str:
    return format(seconds, "f")
