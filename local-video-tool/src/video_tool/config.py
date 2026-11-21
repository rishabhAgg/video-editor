from importlib.resources import files
import json
from pathlib import Path


def read_config(name):
    local = Path("config") / name
    bundled = files("video_tool").joinpath("defaults", name)
    return json.loads((local if local.is_file() else bundled).read_text(encoding="utf-8"))
