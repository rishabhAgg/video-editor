"""Subtitle mapping for transcoded splits."""


def split_subtitle_options(suffix: str) -> list[str]:
    options = ["-map", "0:s?", "-c:s", "copy"]
    if suffix.lower() == ".mkv":
        options += ["-map", "0:t?", "-c:t", "copy"]  # Include fonts for styled subtitles.
    return options
