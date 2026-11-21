import argparse
from dataclasses import asdict
import json
import logging
from pathlib import Path
import re

from .config import read_config
from .exceptions import VideoToolError
from .media.probe import probe
from .utils.logging import configure


def _size(value):
    match = re.fullmatch(r"([1-9][0-9]*)[x×]([1-9][0-9]*)", value)
    if not match:
        raise argparse.ArgumentTypeError("expected WIDTHxHEIGHT")
    return int(match[1]), int(match[2])


def parser():
    root = argparse.ArgumentParser(description="Local HDR gameplay video toolkit")
    commands = root.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("probe", help="inspect streams and HDR metadata")
    inspect.add_argument("input", type=Path)
    split = commands.add_parser("split", help="export a JSON list of timestamped segments")
    split.add_argument("--input", type=Path, required=True)
    split.add_argument("--segments", type=Path, required=True)
    split.add_argument("--output-dir", type=Path)
    split.add_argument("--mode", choices=("fast", "accurate"), default="fast",
                       help="fast copies streams and is limited by keyframes")
    split.add_argument("--preset")
    merge = commands.add_parser("merge", help="merge compatible recordings or normalize explicitly")
    merge.add_argument("--input", type=Path, nargs="+", required=True)
    merge.add_argument("--output", type=Path, required=True)
    merge.add_argument("--normalize", action="store_true")
    merge.add_argument("--color", choices=("reject", "hdr", "sdr"), default="reject",
                       help="sdr explicitly tone-maps HDR10; hdr does not relabel SDR")
    merge.add_argument("--fps", help="target rate, e.g. 60000/1001 (requires --normalize)")
    merge.add_argument("--size", type=_size, help="target WIDTHxHEIGHT (requires --normalize)")
    merge.add_argument("--preset")
    crop = commands.add_parser("crop", help="export a saved framing project")
    crop.add_argument("--project", type=Path, required=True)
    crop.add_argument("--output", type=Path, required=True)
    crop.add_argument("--preset")
    editor = commands.add_parser("editor", help="open the native crop editor")
    editor.add_argument("source", nargs="?", type=Path, help="video or project JSON")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        settings = read_config("settings.json")
        configure(settings.get("log_level", "INFO"))
        if args.command == "probe":
            print(json.dumps(asdict(probe(args.input)), indent=2))
        elif args.command == "split":
            from .media.split import split

            for path in split(args.input, args.segments,
                              args.output_dir or Path(settings.get("split_output", "output/clips")),
                              mode=args.mode, preset=args.preset):
                print(path)
        elif args.command == "merge":
            from .media.merge import merge

            print(merge(args.input, args.output, normalize=args.normalize, color=args.color,
                        fps=args.fps, size=args.size, preset=args.preset))
        elif args.command == "crop":
            from .crop.keyframes import Project
            from .media.encode import export_crop

            print(export_crop(Project.load(args.project), args.output, args.preset))
        else:
            try:
                from .ui.main_window import launch
            except ImportError as exc:
                raise VideoToolError("Install the editor dependencies with pip install -e '.[editor]'.") from exc
            return launch(args.source)
    except (OSError, VideoToolError, ValueError) as exc:
        logging.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        logging.warning("Canceled")
        return 130
    return 0
