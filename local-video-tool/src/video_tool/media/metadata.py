from fractions import Fraction

MASTER_FIELDS = tuple(f"{color}_{axis}" for color in ("red", "green", "blue", "white_point")
                      for axis in ("x", "y")) + ("min_luminance", "max_luminance")


def classify(depth, primaries, transfer, matrix, *, hdr10_plus=False, dolby_vision=False,
             static_metadata=False):
    if all((depth >= 10, primaries == "bt2020", matrix == "bt2020nc")):
        if transfer == "smpte2084":
            return "Dolby Vision" if dolby_vision else "HDR10+" if hdr10_plus else "HDR10"
        if transfer == "arib-std-b67" and not (hdr10_plus or dolby_vision):
            return "HLG"
    if dolby_vision or hdr10_plus or static_metadata or not all((depth, primaries, transfer, matrix)):
        return "UNKNOWN"
    sdr_transfer = {"bt709", "bt470m", "gamma22", "gamma28", "smpte170m", "smpte240m",
                    "iec61966-2-1", "bt2020-10", "bt2020-12"}
    if transfer in sdr_transfer and (primaries == "bt2020") == matrix.startswith("bt2020"):
        return None
    return "UNKNOWN"


def mastering_signature(data):
    if data is None:
        return None
    values = tuple(Fraction(data[key]) for key in MASTER_FIELDS)
    if any(value < 0 for value in values) or any(value > 1 for value in values[:8]):
        raise ValueError("Invalid mastering display chromaticities or luminance")
    if not values[-2] < values[-1]:
        raise ValueError("Invalid mastering display luminance bounds")
    return values


def x265_mastering(data):
    values = mastering_signature(data)
    if values is None:
        return None
    r, g, b, white = [tuple(values[i + j] * 50000 for j in range(2)) for i in range(0, 8, 2)]
    minimum, maximum = (value * 10000 for value in values[8:])
    if any(value.denominator != 1 for value in (*r, *g, *b, *white, minimum, maximum)):
        raise ValueError("Mastering display values cannot be represented exactly by x265")
    return (f"G({g[0]},{g[1]})B({b[0]},{b[1]})R({r[0]},{r[1]})"
            f"WP({white[0]},{white[1]})L({maximum},{minimum})")
