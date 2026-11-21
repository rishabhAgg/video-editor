from dataclasses import dataclass, field


@dataclass
class MediaInfo:
    path: str
    duration: float
    width: int
    height: int
    fps: str
    video_codec: str
    pixel_format: str
    bit_depth: int
    color_primaries: str | None = None
    color_transfer: str | None = None
    color_space: str | None = None
    is_hdr: bool = False
    hdr_format: str | None = "UNKNOWN"
    audio_streams: list[dict] = field(default_factory=list)
    video_streams: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    average_fps: str = "0/0"
    profile: str | None = None
    time_base: str = "0/0"
    start_time: float | None = None
    frame_count: int | None = None
    color_range: str | None = None
    chroma_subsampling: str | None = None
    mastering_display: dict | None = None
    max_cll: int | None = None
    max_fall: int | None = None
    hdr10_plus: bool = False
    dolby_vision: bool = False
    warnings: list[str] = field(default_factory=list)
