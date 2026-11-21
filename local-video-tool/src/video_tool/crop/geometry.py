"""Source-space crop geometry independent of FFmpeg."""

from dataclasses import dataclass
from math import floor, isfinite


@dataclass(frozen=True)
class CropRect:
    x: int
    y: int
    width: int
    height: int


def _even_offset(center: float, source: int, extent: int) -> int:
    offset = min(max(center * source - extent / 2, 0), source - extent)
    return 2 * floor(offset / 2)


def geometry(
    width: int,
    height: int,
    target_width: int,
    target_height: int,
    center_x: float = 0.5,
    center_y: float = 0.5,
) -> CropRect:
    """Return the largest approximately aspect-matched, even-aligned viewport."""
    dimensions = (width, height, target_width, target_height)
    if any(type(value) is not int or value < 2 for value in dimensions):
        raise ValueError("crop dimensions must be integers of at least 2 pixels")
    if any(
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not 0 <= value <= 1
        or not isfinite(value)
        for value in (center_x, center_y)
    ):
        raise ValueError("crop centers must be between 0 and 1")

    crop_width, crop_height = width // 2 * 2, height // 2 * 2
    if crop_width * target_height >= crop_height * target_width:
        crop_width = max(2, ((crop_height * target_width + target_height) // (2 * target_height)) * 2)
    else:
        crop_height = max(2, ((crop_width * target_height + target_width) // (2 * target_width)) * 2)
    return CropRect(
        _even_offset(center_x, width, crop_width),
        _even_offset(center_y, height, crop_height),
        crop_width,
        crop_height,
    )
