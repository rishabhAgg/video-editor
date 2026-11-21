"""Versioned crop project and timeline persistence."""

from dataclasses import asdict, dataclass, field
from fractions import Fraction
import json
from math import gcd, isfinite
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

MODES = ("dynamic_crop", "blurred_background", "dynamic_crop_blurred_background")
METHODS = ("smooth", "linear", "hold")


def _finite(value: object) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return isfinite(value)
    except OverflowError:
        return False


@dataclass(frozen=True)
class Keyframe:
    time: float
    center_x: float
    center_y: float = 0.5


@dataclass
class Project:
    source: str
    output_width: int = 1080
    output_height: int = 1920
    mode: str = "dynamic_crop"
    interpolation: str = "smooth"
    keyframes: list[Keyframe] = field(default_factory=list)
    blur: int = 20
    inset: float | None = None
    dark_overlay: float | None = None
    source_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def output_aspect_ratio(self) -> str:
        divisor = gcd(self.output_width, self.output_height)
        return f"{self.output_width // divisor}:{self.output_height // divisor}"

    @property
    def foreground_fraction(self) -> float:
        if self.inset is not None:
            return self.inset
        return 0.85 if self.mode == "dynamic_crop_blurred_background" else 1.0

    def validate(self, duration: float | None = None) -> None:
        if not isinstance(self.source, str) or not self.source.strip() or "\0" in self.source:
            raise ValueError("source must be a nonempty path")
        for value in (self.output_width, self.output_height):
            if type(value) is not int or value < 2 or value % 2:
                raise ValueError("output dimensions must be positive even integers")
        if self.mode not in MODES:
            raise ValueError(f"unknown crop mode: {self.mode!r}")
        if self.interpolation not in METHODS:
            raise ValueError(f"unknown interpolation: {self.interpolation!r}")
        if type(self.blur) is not int or self.blur < 0:
            raise ValueError("blur must be a nonnegative integer")
        if self.inset is not None and (not _finite(self.inset) or not 0 < self.inset <= 1):
            raise ValueError("inset must be greater than 0 and at most 1")
        if self.dark_overlay is not None and (
            not _finite(self.dark_overlay) or not 0 <= self.dark_overlay <= 1
        ):
            raise ValueError("dark_overlay must be between 0 and 1")
        if not isinstance(self.source_metadata, dict):
            raise ValueError("source_metadata must be an object")
        if duration is not None and (not _finite(duration) or duration < 0):
            raise ValueError("duration must be finite and nonnegative")
        if not isinstance(self.keyframes, list):
            raise ValueError("keyframes must be a list")
        previous = -1.0
        for keyframe in self.keyframes:
            if not isinstance(keyframe, Keyframe):
                raise ValueError("keyframes must contain Keyframe values")
            if not _finite(keyframe.time) or keyframe.time < 0 or keyframe.time <= previous:
                raise ValueError("keyframe timestamps must be nonnegative and strictly increasing")
            if duration is not None and keyframe.time > duration:
                raise ValueError("keyframe timestamp exceeds source duration")
            for center in (keyframe.center_x, keyframe.center_y):
                if not _finite(center) or not 0 <= center <= 1:
                    raise ValueError("keyframe centers must be between 0 and 1")
            previous = keyframe.time

    @classmethod
    def load(cls, path: Path) -> "Project":
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
            raise ValueError("expected a version 1 crop project")
        del data["version"]
        aspect = data.pop("output_aspect_ratio", None)
        try:
            keyframes = data.pop("keyframes", [])
            if not isinstance(keyframes, list):
                raise ValueError("keyframes must be a list")
            project = cls(**data, keyframes=[Keyframe(**item) for item in keyframes])
        except TypeError as error:
            raise ValueError(f"invalid crop project: {error}") from error
        project.validate()
        if aspect is not None:
            try:
                if not isinstance(aspect, str):
                    raise ValueError
                numerator, denominator = aspect.split(":")
                ratio = Fraction(int(numerator), int(denominator))
            except (ValueError, ZeroDivisionError) as error:
                raise ValueError("invalid output_aspect_ratio") from error
            if ratio != Fraction(project.output_width, project.output_height):
                raise ValueError("output_aspect_ratio does not match output dimensions")
        project.source = str((path.parent / Path(project.source).expanduser()).resolve())
        return project

    def save(self, path: Path) -> None:
        self.validate()
        path = Path(path)
        data = {"version": 1, "output_aspect_ratio": self.output_aspect_ratio, **asdict(self)}
        source = Path(self.source).expanduser()
        if source.is_absolute() and source.is_relative_to(path.parent.resolve()):
            data["source"] = str(source.relative_to(path.parent.resolve()))
        source = path.parent / source
        if path.is_symlink() or path.resolve() == source.resolve():
            raise ValueError(f"Refusing to overwrite a source recording or symlink: {path}")
        existing = path.is_file()
        if existing:
            if source.is_file() and path.samefile(source):
                raise ValueError(f"Refusing to overwrite a source recording: {path}")
            try:
                self.load(path)
            except (OSError, ValueError) as error:
                raise ValueError(f"Refusing to overwrite a file that is not a crop project: {path}") from error
        if not self.source_metadata and source.is_file():
            from ..media.probe import probe

            info = probe(source)
            self.validate(info.duration)
            data["source_metadata"] = asdict(info)
        try:
            payload = json.dumps(data, indent=2, allow_nan=False)
        except TypeError as error:
            raise ValueError(f"invalid crop project: {error}") from error
        with TemporaryDirectory(prefix=".crop-project-", dir=path.parent) as directory:
            staged = Path(directory) / "project.json"
            staged.write_text(payload + "\n", encoding="utf-8")
            if existing:
                staged.replace(path)
            else:
                os.link(staged, path)
