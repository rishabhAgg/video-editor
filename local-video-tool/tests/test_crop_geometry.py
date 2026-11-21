import pytest

from video_tool.crop.geometry import CropRect, geometry


def test_landscape_crop_follows_horizontal_center_and_clamps_to_edges():
    assert geometry(1920, 1080, 1080, 1920, 0) == CropRect(0, 0, 608, 1080)
    assert geometry(1920, 1080, 1080, 1920) == CropRect(656, 0, 608, 1080)
    assert geometry(1920, 1080, 1080, 1920, 1) == CropRect(1312, 0, 608, 1080)


def test_odd_portrait_source_follows_vertical_center_and_stays_in_bounds():
    rect = geometry(721, 1281, 16, 9, 0.5, 1)
    assert rect == CropRect(0, 874, 720, 406)
    assert rect.y + rect.height <= 1281


@pytest.mark.parametrize("args", [(0, 1080, 9, 16), (1920, 1080, 9, 16, float("nan"))])
def test_invalid_geometry(args):
    with pytest.raises(ValueError):
        geometry(*args)
