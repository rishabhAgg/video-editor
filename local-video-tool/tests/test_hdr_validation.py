from dataclasses import replace

import pytest

from video_tool.exceptions import VideoToolError
from video_tool.media.encode import encode_options
from video_tool.media.probe import probe
from video_tool.media.validation import validate_export


def test_detects_lost_side_data_even_when_color_tags_match(pq_source):
    info = probe(pq_source)
    with pytest.raises(VideoToolError, match="changed color signaling"):
        validate_export(info, replace(info, max_cll=None))


def test_hdr_preset_cannot_relabel_sdr(pq_source):
    info = replace(probe(pq_source), is_hdr=False, hdr_format=None, bit_depth=8,
                   color_primaries="bt709", color_transfer="bt709", color_space="bt709")
    with pytest.raises(VideoToolError, match="does not match"):
        encode_options(info, "instagram_hdr")
