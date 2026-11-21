"""Build FFmpeg filtergraphs for the three crop layouts."""

import re

from ..crop.geometry import geometry
from ..crop.keyframes import Project


def _number(value: float) -> str:
    return repr(float(value))


def _coordinate(project: Project, axis: str, size: int, extent: int) -> str:
    keys = project.keyframes
    if extent == size:
        return "0"
    points = [_number(getattr(key, f"center_{axis}") * size - extent / 2) for key in keys]
    if not keys:
        return str((size - extent) // 4 * 2)

    def segment(index: int) -> str:
        start = points[index]
        if project.interpolation == "hold" or points[index + 1] == start:
            return start
        fraction = f"(t-{_number(keys[index].time)})/{_number(keys[index + 1].time - keys[index].time)}"
        delta = _number(float(points[index + 1]) - float(start))
        if project.interpolation == "linear":
            return f"{start}+({delta})*({fraction})"
        return f"st(0,{fraction});{start}+({delta})*ld(0)*ld(0)*(3-2*ld(0))"

    def tree(first: int, last: int) -> str:
        if first == last:
            return segment(first)
        middle = (first + last + 1) // 2
        return f"if(lt(t,{_number(keys[middle].time)}),{tree(first, middle - 1)},{tree(middle, last)})"

    center = points[0]
    if len(keys) > 1 and any(point != center for point in points[1:]):
        center = (
            f"if(lt(t,{_number(keys[0].time)}),{center},"
            f"if(gte(t,{_number(keys[-1].time)}),{points[-1]},{tree(0, len(keys) - 2)}))"
        )
    return f"2*floor(clip({center},0,{size - extent})/2)"


def _dynamic(project: Project, width: int, height: int) -> str:
    rect = geometry(width, height, project.output_width, project.output_height)
    x = _coordinate(project, "x", width, rect.width)
    y = _coordinate(project, "y", height, rect.height)
    return f"crop=w={rect.width}:h={rect.height}:x='{x}':y='{y}':exact=1"


def _scale(width: int, height: int, *, cover: bool) -> str:
    comparison = "gt" if cover else "lt"
    rounding = "ceil" if cover else "floor"
    w = f"if({comparison}(dar,{width}/{height}),max(2,2*{rounding}({height}*dar/2)),{width})"
    h = f"if({comparison}({width}/{height},dar),max(2,2*{rounding}({width}/dar/2)),{height})"
    return f"scale=w='{w}':h='{h}':flags=lanczos,setsar=1"


def _precision(pixel_format: str) -> int:
    if not isinstance(pixel_format, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", pixel_format):
        raise ValueError("invalid FFmpeg pixel format")
    if match := re.search(r"(?:p0?|gray|xyz)(9|10|12|14|16)(?:le|be)$", pixel_format):
        return int(match[1])
    if pixel_format.startswith(("rgb48", "bgr48", "rgba64", "bgra64", "ya16")):
        return 16
    if re.search(r"(?:p[124](?:0)?|[xy]v?)(10|12|16)(?:le|be)$", pixel_format):
        return int(pixel_format[-4:-2])
    if pixel_format.startswith(("x2rgb10", "x2bgr10", "v210", "y210")):
        return 10
    return 8


def _dark_band(parts: list[str], height: int, opacity: float, depth: int) -> None:
    top = (height * 3 // 8) * 2
    fraction = _number(opacity)
    midpoint = 1 << (depth - 1)
    dark = (
        f"lutyuv=y='val*(1-{fraction})+minval*{fraction}':"
        f"u='val*(1-{fraction})+{midpoint}*{fraction}':"
        f"v='val*(1-{fraction})+{midpoint}*{fraction}'"
    )
    if not top:
        parts.append(f"[background]{dark}[darkened]")
        return
    parts.extend((
        "[background]split[upper][lower]",
        f"[upper]crop=iw:{top}:0:0[top]",
        f"[lower]crop=iw:{height - top}:0:{top},{dark}[band]",
        "[top][band]vstack[darkened]",
    ))


def filtergraph(project: Project, width: int, height: int, pixel_format: str) -> str:
    """Return a graph from [0:v:0] to [v], using seconds since the first frame."""
    project.validate()
    canvas_w, canvas_h = project.output_width, project.output_height
    geometry(width, height, canvas_w, canvas_h)
    depth = _precision(pixel_format)
    working_format = "yuv420p" if depth == 8 else f"yuv420p{depth}le"
    source = f"[0:v:0]setpts=PTS-STARTPTS,format={working_format}"
    cover = f"{_scale(canvas_w, canvas_h, cover=True)},crop={canvas_w}:{canvas_h}"
    if project.mode == "dynamic_crop":
        return f"{source},{_dynamic(project, width, height)},{cover}[v]"

    parts = [f"{source},split[back][front]"]
    radius = min(project.blur * canvas_w // 1080, min(canvas_w, canvas_h) // 2)
    blur = f",boxblur=lr={radius}:cr={radius // 2}" if radius else ""
    parts.append(f"[back]{cover}{blur}[background]")
    background = "background"
    if project.dark_overlay:
        _dark_band(parts, canvas_h, project.dark_overlay, depth)
        background = "darkened"

    fraction = project.foreground_fraction
    front_w = max(2, int(canvas_w * fraction) // 2 * 2)
    front_h = max(2, int(canvas_h * fraction) // 2 * 2)
    crop = f"{_dynamic(project, width, height)}," if project.mode == "dynamic_crop_blurred_background" else ""
    parts.append(f"[front]{crop}{_scale(front_w, front_h, cover=False)}[foreground]")
    center = "x=2*floor((main_w-overlay_w)/4):y=2*floor((main_h-overlay_h)/4)"
    if depth in (8, 10):
        overlay = "yuv420p10" if depth == 10 else "yuv420"
        parts.append(f"[{background}][foreground]overlay={center}:format={overlay}:shortest=1[v]")
    else:
        # Overlay supports only 8- and 10-bit YUV; inherit the foreground shape via a padded mask.
        pad = f"pad={canvas_w}:{canvas_h}:'2*floor((ow-iw)/4)':'2*floor((oh-ih)/4)'"
        threshold = 3 * (1 << depth) // 4
        mask = f"if(gt(val,{threshold}),maxval,0)"
        parts.extend((
            "[foreground]split[image][shape]",
            f"[image]{pad}[padded]",
            f"[shape]lutyuv=y=maxval:u=maxval:v=maxval,{pad},lutyuv=y='{mask}':u='{mask}':v='{mask}'[mask]",
            f"[{background}][padded][mask]maskedmerge[v]",
        ))
    return ";".join(parts)
