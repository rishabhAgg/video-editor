import os
from pathlib import Path
import subprocess
import tempfile
import time

from ..exceptions import VideoToolError


def capture(binary, args):
    if binary == "ffprobe":
        args = ["-protocol_whitelist", "file,pipe", *args]
    try:
        result = subprocess.run([binary, "-v", "error", *args], capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
    except FileNotFoundError as exc:
        raise VideoToolError(f"{binary} not found on PATH") from exc
    if result.returncode:
        raise VideoToolError(f"{binary}: {result.stderr.strip()}")
    return result.stdout


def encode(args, output: Path, duration, progress=None, cancel=None):
    if cancel and cancel():
        raise VideoToolError("Export canceled")
    local_args = [part for arg in args for part in (
        ("-protocol_whitelist", "file,pipe", "-i") if arg == "-i" else (arg,)
    )]
    strict = ["-xerror"] if "copy" not in args else []
    with tempfile.TemporaryFile() as errors, tempfile.TemporaryFile() as status:
        try:
            process = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin", *strict,
                                        "-progress", "pipe:1", "-n", *local_args, str(output)],
                                       stdout=status, stderr=errors)
        except FileNotFoundError as exc:
            raise VideoToolError("ffmpeg not found on PATH") from exc
        try:
            offset = 0
            pending = b""
            while process.poll() is None:
                if cancel and cancel():
                    raise VideoToolError("Export canceled")
                if progress and duration:
                    chunk = os.pread(status.fileno(), 65536, offset)
                    offset += len(chunk)
                    *lines, pending = (pending + chunk).split(b"\n")
                    for line in lines:
                        if line.startswith(b"out_time_us=") and line[12:].lstrip(b"-").isdigit():
                            progress(max(0.0, min(int(line[12:]) / 1_000_000 / duration, 0.99)))
                time.sleep(0.1)
            errors.seek(0)
            stderr = errors.read().decode(errors="replace").strip()
            if process.returncode:
                raise VideoToolError(f"FFmpeg failed: {stderr}")
            if "non-monoton" in stderr.lower():
                return ["FFmpeg adjusted non-monotonic stream timestamps during muxing."]
            return []
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
