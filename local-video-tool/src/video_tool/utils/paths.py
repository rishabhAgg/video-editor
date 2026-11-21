from pathlib import Path

from ..exceptions import VideoToolError


def output_paths(path: Path):
    path = path.expanduser().absolute()
    if not path.suffix:
        raise VideoToolError("An output filename with a container extension is required.")
    report = path.with_name(f"{path.name}.report.json")
    for candidate in (path, report):
        if candidate.exists() or candidate.is_symlink():
            raise VideoToolError(f"Refusing to overwrite {candidate}")
    return path, report
