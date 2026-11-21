from video_tool.media.metadata import classify


def test_detect_dynamic_metadata_without_confusing_hlg_and_pq():
    assert classify(10, "bt2020", "arib-std-b67", "bt2020nc") == "HLG"
    assert classify(10, "bt2020", "smpte2084", "bt2020nc", hdr10_plus=True) == "HDR10+"
    assert classify(10, "bt2020", "smpte2084", "bt2020nc", dolby_vision=True) == "Dolby Vision"
    assert classify(10, "bt2020", "bt709", "bt709") == "UNKNOWN"
