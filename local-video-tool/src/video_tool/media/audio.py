"""Audio stream mapping and normalization used by media operations."""


def split_audio_options() -> list[str]:
    return ["-map", "0:a?", "-c:a", "aac"]


def audio_filters(
    input_no: int, track_count: int, output_tracks: int, duration: float, video_offset: float
) -> list[str]:
    filters = []
    for track in range(output_tracks):
        output = f"[a{input_no}_{track}]"
        if track < track_count:
            filters.append(
                f"[{input_no}:a:{track}]asetpts=PTS-({video_offset:.6f})/TB,"
                "aresample=48000:async=1:first_pts=0,"
                "aformat=sample_fmts=fltp:channel_layouts=stereo,"
                f"apad,atrim=duration={duration:.6f}{output}"
            )
        else:
            filters.append(f"anullsrc=r=48000:cl=stereo:d={duration:.6f}{output}")
    return filters
