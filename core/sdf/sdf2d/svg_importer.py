"""
core/sdf/sdf2d/svg_importer.py

Parses an SVG file (or string) into a list of Sdf2dBezierCurve instances.
No rasterization — all primitives are converted to cubic Bezier segments
analytically. Supported elements: <path>, <rect>, <circle>, <ellipse>,
<line>, <polyline>, <polygon>. CSS, gradients, transforms, and groups
are NOT yet supported (only top-level shapes with no transform).

Public API:
    parse_svg(source)   -> list[Sdf2dBezierCurve]
    parse_path_d(d_str) -> list[list[(p0, p1, p2, p3)]]    # one list per subpath
"""

import re
import math
import xml.etree.ElementTree as ET
from .bezier_curve import Sdf2dBezierCurve

_CMD_RE = re.compile(r"([MmLlHhVvCcSsQqTtAaZz])")
_NUM_RE = re.compile(r"[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?")


def _tokenize_path(d: str):
    """Split an SVG path 'd' attribute into [(cmd_letter, [floats]), ...]."""
    tokens = []
    parts = _CMD_RE.split(d)
    i = 1
    while i < len(parts):
        cmd = parts[i]
        args = parts[i + 1] if i + 1 < len(parts) else ""
        nums = [float(m.group(0)) for m in _NUM_RE.finditer(args)]
        tokens.append((cmd, nums))
        i += 2
    return tokens


def _line_as_cubic(p0, p1):
    return (
        p0,
        (p0[0] + (p1[0] - p0[0]) / 3.0, p0[1] + (p1[1] - p0[1]) / 3.0),
        (p0[0] + 2 * (p1[0] - p0[0]) / 3.0, p0[1] + 2 * (p1[1] - p0[1]) / 3.0),
        p1,
    )


def _quad_to_cubic(p0, p1, p2):
    return (
        p0,
        (p0[0] + 2.0 / 3.0 * (p1[0] - p0[0]), p0[1] + 2.0 / 3.0 * (p1[1] - p0[1])),
        (p2[0] + 2.0 / 3.0 * (p1[0] - p2[0]), p2[1] + 2.0 / 3.0 * (p1[1] - p2[1])),
        p2,
    )


def _arc_endpoint_to_center(p1, p2, rx, ry, phi_deg, fa, fs):
    """Convert SVG arc endpoint parameters to center parameterization.
    Returns (cx, cy, theta1, dtheta, rx, ry, phi) or None if degenerate."""
    if rx == 0 or ry == 0 or (p1[0] == p2[0] and p1[1] == p2[1]):
        return None
    rx, ry = abs(rx), abs(ry)
    phi = math.radians(phi_deg)
    cos_phi, sin_phi = math.cos(phi), math.sin(phi)

    dx = (p1[0] - p2[0]) / 2.0
    dy = (p1[1] - p2[1]) / 2.0
    x1p =  cos_phi * dx + sin_phi * dy
    y1p = -sin_phi * dx + cos_phi * dy

    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1.0:
        s = math.sqrt(lam)
        rx *= s; ry *= s

    sign = -1.0 if fa == fs else 1.0
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    coeff = sign * math.sqrt(max(num, 0.0) / den) if den > 0 else 0.0
    cxp =  coeff * (rx * y1p) / ry
    cyp = -coeff * (ry * x1p) / rx

    cx = cos_phi * cxp - sin_phi * cyp + (p1[0] + p2[0]) / 2.0
    cy = sin_phi * cxp + cos_phi * cyp + (p1[1] + p2[1]) / 2.0

    def _angle(u, v):
        d = u[0] * v[0] + u[1] * v[1]
        l = math.sqrt((u[0] ** 2 + u[1] ** 2) * (v[0] ** 2 + v[1] ** 2))
        a = math.acos(max(-1.0, min(1.0, d / l))) if l > 0 else 0.0
        if u[0] * v[1] - u[1] * v[0] < 0:
            a = -a
        return a

    v1 = ((x1p - cxp) / rx, (y1p - cyp) / ry)
    v2 = ((-x1p - cxp) / rx, (-y1p - cyp) / ry)
    theta1 = _angle((1.0, 0.0), v1)
    dtheta = _angle(v1, v2)
    if not fs and dtheta > 0:
        dtheta -= 2.0 * math.pi
    elif fs and dtheta < 0:
        dtheta += 2.0 * math.pi

    return (cx, cy, theta1, dtheta, rx, ry, phi)


def _arc_to_cubics(p1, p2, rx, ry, phi_deg, fa, fs):
    """Return a list of cubic Bezier tuples approximating the SVG arc."""
    params = _arc_endpoint_to_center(p1, p2, rx, ry, phi_deg, fa, fs)
    if params is None:
        return [_line_as_cubic(p1, p2)]
    cx, cy, theta1, dtheta, rx, ry, phi = params
    cos_phi, sin_phi = math.cos(phi), math.sin(phi)

    n = max(1, int(math.ceil(abs(dtheta) / (math.pi / 2.0))))
    delta = dtheta / n
    alpha = (4.0 / 3.0) * math.tan(delta / 4.0)

    def _xform(ux, uy):
        x = ux * rx
        y = uy * ry
        return (cos_phi * x - sin_phi * y + cx,
                sin_phi * x + cos_phi * y + cy)

    segs = []
    cur_theta = theta1
    for _ in range(n):
        t1 = cur_theta
        t2 = cur_theta + delta
        cos1, sin1 = math.cos(t1), math.sin(t1)
        cos2, sin2 = math.cos(t2), math.sin(t2)
        seg = (
            _xform(cos1, sin1),
            _xform(cos1 - alpha * sin1, sin1 + alpha * cos1),
            _xform(cos2 + alpha * sin2, sin2 - alpha * cos2),
            _xform(cos2, sin2),
        )
        segs.append(seg)
        cur_theta = t2
    return segs


def parse_path_d(d: str):
    """Convert one SVG 'd' attribute into subpaths of cubic Beziers.
    Returns: list[list[(p0, p1, p2, p3)]] — outer list = subpaths, inner = segments.
    Each point is a (x, y) tuple of floats. Open subpaths are closed by appending
    a straight-line cubic Bezier from end → start.
    """
    tokens = _tokenize_path(d)
    subpaths = []
    current = []
    cp = (0.0, 0.0)
    sp = (0.0, 0.0)
    prev_ctrl = None
    prev_q_ctrl = None

    def _close_subpath():
        nonlocal current, cp
        if current:
            end = current[-1][3]
            if abs(end[0] - sp[0]) > 1e-9 or abs(end[1] - sp[1]) > 1e-9:
                current.append(_line_as_cubic(end, sp))
            subpaths.append(current)
        current = []
        cp = sp

    for cmd, nums in tokens:
        rel = cmd.islower()

        if cmd in ("M", "m"):
            j = 0
            first_of_cmd = True
            while j + 1 < len(nums):
                x, y = nums[j], nums[j + 1]; j += 2
                if rel:
                    x += cp[0]; y += cp[1]
                if first_of_cmd:
                    if current:
                        subpaths.append(current)
                        current = []
                    cp = (x, y)
                    sp = cp
                    first_of_cmd = False
                else:
                    new = (x, y)
                    current.append(_line_as_cubic(cp, new))
                    cp = new
            prev_ctrl = None
            prev_q_ctrl = None

        elif cmd in ("L", "l"):
            j = 0
            while j + 1 < len(nums):
                x, y = nums[j], nums[j + 1]; j += 2
                if rel: x += cp[0]; y += cp[1]
                new = (x, y)
                current.append(_line_as_cubic(cp, new))
                cp = new
            prev_ctrl = None
            prev_q_ctrl = None

        elif cmd in ("H", "h"):
            for x in nums:
                xa = x + cp[0] if rel else x
                new = (xa, cp[1])
                current.append(_line_as_cubic(cp, new))
                cp = new
            prev_ctrl = None
            prev_q_ctrl = None

        elif cmd in ("V", "v"):
            for y in nums:
                ya = y + cp[1] if rel else y
                new = (cp[0], ya)
                current.append(_line_as_cubic(cp, new))
                cp = new
            prev_ctrl = None
            prev_q_ctrl = None

        elif cmd in ("Z", "z"):
            _close_subpath()
            prev_ctrl = None
            prev_q_ctrl = None

        elif cmd in ("C", "c"):
            j = 0
            while j + 5 < len(nums):
                x1, y1, x2, y2, x, y = nums[j:j + 6]; j += 6
                if rel:
                    x1 += cp[0]; y1 += cp[1]
                    x2 += cp[0]; y2 += cp[1]
                    x  += cp[0]; y  += cp[1]
                seg = (cp, (x1, y1), (x2, y2), (x, y))
                current.append(seg)
                prev_ctrl = (x2, y2)
                cp = (x, y)
            prev_q_ctrl = None

        elif cmd in ("S", "s"):
            j = 0
            while j + 3 < len(nums):
                x2, y2, x, y = nums[j:j + 4]; j += 4
                if rel:
                    x2 += cp[0]; y2 += cp[1]
                    x  += cp[0]; y  += cp[1]
                if prev_ctrl is not None:
                    x1 = 2 * cp[0] - prev_ctrl[0]
                    y1 = 2 * cp[1] - prev_ctrl[1]
                else:
                    x1, y1 = cp
                seg = (cp, (x1, y1), (x2, y2), (x, y))
                current.append(seg)
                prev_ctrl = (x2, y2)
                cp = (x, y)
            prev_q_ctrl = None

        elif cmd in ("Q", "q"):
            j = 0
            while j + 3 < len(nums):
                x1, y1, x, y = nums[j:j + 4]; j += 4
                if rel:
                    x1 += cp[0]; y1 += cp[1]
                    x  += cp[0]; y  += cp[1]
                qctrl = (x1, y1)
                current.append(_quad_to_cubic(cp, qctrl, (x, y)))
                prev_q_ctrl = qctrl
                cp = (x, y)
            prev_ctrl = None

        elif cmd in ("T", "t"):
            j = 0
            while j + 1 < len(nums):
                x, y = nums[j:j + 2]; j += 2
                if rel: x += cp[0]; y += cp[1]
                if prev_q_ctrl is not None:
                    qctrl = (2 * cp[0] - prev_q_ctrl[0], 2 * cp[1] - prev_q_ctrl[1])
                else:
                    qctrl = cp
                current.append(_quad_to_cubic(cp, qctrl, (x, y)))
                prev_q_ctrl = qctrl
                cp = (x, y)
            prev_ctrl = None

        elif cmd in ("A", "a"):
            j = 0
            while j + 6 < len(nums):
                rx, ry, x_rot_deg, large_arc, sweep, x, y = nums[j:j + 7]; j += 7
                if rel: x += cp[0]; y += cp[1]
                segs = _arc_to_cubics(cp, (x, y), rx, ry, x_rot_deg,
                                      bool(int(large_arc)), bool(int(sweep)))
                current.extend(segs)
                cp = (x, y)
            prev_ctrl = None
            prev_q_ctrl = None

        else:
            raise ValueError(f"unknown SVG path command: {cmd}")

    if current:
        subpaths.append(current)
    return subpaths


SVG_NS = "{http://www.w3.org/2000/svg}"


def _shape_to_subpaths(elem):
    """Convert one SVG shape element to a list of subpaths
    (each subpath = list of cubic Bezier tuples)."""
    tag = elem.tag.replace(SVG_NS, "")
    if tag == "path":
        d = elem.get("d", "")
        return parse_path_d(d) if d else []
    if tag == "rect":
        x = float(elem.get("x", 0)); y = float(elem.get("y", 0))
        w = float(elem.get("width", 0)); h = float(elem.get("height", 0))
        if w <= 0 or h <= 0:
            return []
        d = f"M {x} {y} h {w} v {h} h {-w} z"
        return parse_path_d(d)
    if tag == "circle":
        cx = float(elem.get("cx", 0)); cy = float(elem.get("cy", 0))
        r  = float(elem.get("r", 0))
        if r <= 0:
            return []
        d = f"M {cx - r} {cy} a {r} {r} 0 1 0 {2 * r} 0 a {r} {r} 0 1 0 {-2 * r} 0 z"
        return parse_path_d(d)
    if tag == "ellipse":
        cx = float(elem.get("cx", 0)); cy = float(elem.get("cy", 0))
        rx = float(elem.get("rx", 0)); ry = float(elem.get("ry", 0))
        if rx <= 0 or ry <= 0:
            return []
        d = f"M {cx - rx} {cy} a {rx} {ry} 0 1 0 {2 * rx} 0 a {rx} {ry} 0 1 0 {-2 * rx} 0 z"
        return parse_path_d(d)
    if tag == "line":
        x1 = float(elem.get("x1", 0)); y1 = float(elem.get("y1", 0))
        x2 = float(elem.get("x2", 0)); y2 = float(elem.get("y2", 0))
        d = f"M {x1} {y1} L {x2} {y2}"
        return parse_path_d(d)
    if tag in ("polyline", "polygon"):
        pts = re.findall(r"[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?",
                         elem.get("points", ""))
        if len(pts) < 4:
            return []
        d = "M " + " ".join(pts[:2]) + " L " + " ".join(pts[2:])
        if tag == "polygon":
            d += " Z"
        return parse_path_d(d)
    return []


def parse_svg(source):
    """Parse an SVG file path, file-like, or XML string into a list of Sdf2dBezierCurve.
    Each closed subpath becomes one Sdf2dBezierCurve.

    Note on Y axis: SVG Y points down; FreeCAD profiles assume Y up.
    Callers are responsible for any axis flip (negate all y coordinates).
    """
    if isinstance(source, str) and source.lstrip().startswith("<"):
        root = ET.fromstring(source)
    else:
        root = ET.parse(source).getroot()

    curves = []
    for elem in root.iter():
        for sp in _shape_to_subpaths(elem):
            if sp:
                curves.append(Sdf2dBezierCurve(sp))
    return curves
