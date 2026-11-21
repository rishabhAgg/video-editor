"""Evaluate a crop timeline in presentation-time seconds."""

from bisect import bisect_right
from collections.abc import Sequence

from .keyframes import Keyframe, METHODS


def position(
    keyframes: Sequence[Keyframe], time: float, method: str = "smooth"
) -> tuple[float, float]:
    """Interpolate normalized centers from an ordered, validated timeline."""
    if method not in METHODS:
        raise ValueError(f"unknown interpolation: {method!r}")
    if not keyframes:
        return 0.5, 0.5
    index = bisect_right(keyframes, time, key=lambda keyframe: keyframe.time)
    if index == 0:
        return keyframes[0].center_x, keyframes[0].center_y
    left = keyframes[index - 1]
    if index == len(keyframes) or method == "hold":
        return left.center_x, left.center_y
    right = keyframes[index]
    weight = (time - left.time) / (right.time - left.time)
    if method == "smooth":
        weight = weight * weight * (3 - 2 * weight)
    return (
        left.center_x + (right.center_x - left.center_x) * weight,
        left.center_y + (right.center_y - left.center_y) * weight,
    )
