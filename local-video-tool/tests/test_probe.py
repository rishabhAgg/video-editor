from pathlib import Path

from video_tool.media.probe import from_probe, probe


def test_reads_frame_level_hdr_metadata(pq_source):
    info = probe(pq_source)
    assert (info.hdr_format, info.bit_depth, info.color_space) == ("HDR10", 10, "bt2020nc")
    assert (info.max_cll, info.max_fall) == (773, 210)
    assert info.mastering_display["max_luminance"] == "10000000/10000"
    assert info.video_streams[0]["extradata_hash"].startswith("SHA256:")


def test_contradictory_signal_is_not_hdr():
    info = from_probe(Path("gameplay-hdr.mp4"), {"streams": [{"codec_type": "video", "width": 320,
        "height": 180, "duration": "2", "pix_fmt": "yuv420p", "bits_per_raw_sample": "10",
        "color_primaries": "bt2020", "color_transfer": "smpte2084", "color_space": "bt2020nc"}]})
    assert info.hdr_format == "UNKNOWN" and info.warnings
