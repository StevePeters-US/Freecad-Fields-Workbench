"""core/sdf/profile_tex.py

Bakes a 2D SDF profile to a GL_R32F 2D texture for fast texture-based
sphere tracing in the interactive render path.
"""
import ctypes
import numpy as np
from core.gl_texture3d import _loader

GL_TEXTURE_2D     = 0x0DE1
GL_R32F           = 0x822E
GL_RED            = 0x1903
GL_FLOAT          = 0x1406
GL_LINEAR         = 0x2601
GL_CLAMP_TO_EDGE  = 0x812F
GL_TEXTURE_MIN_FILTER = 0x2801
GL_TEXTURE_MAG_FILTER = 0x2800
GL_TEXTURE_WRAP_S = 0x2802
GL_TEXTURE_WRAP_T = 0x2803


def profile_bbox(field) -> tuple:
    """Compute an expanded 2D bounding box for a profile attached to an SDF field.
    Returns (x0, y0, x1, y1) with 30% margin, suitable for UV mapping.
    For extrusion the space is (x, y); for revolution it is (r, z).
    """
    profile = getattr(field, 'profile', field)
    if hasattr(profile, 'bbox_2d'):
        x0, y0, x1, y1 = profile.bbox_2d()
    else:
        try:
            from core.sdf.sdf_extrusion import _profile_radial_extent
            r = _profile_radial_extent(profile)
        except Exception:
            r = 100.0
        x0, y0, x1, y1 = -r, -r, r, r

    cx = (x0 + x1) * 0.5
    cy = (y0 + y1) * 0.5
    hw = max((x1 - x0) * 0.65, 1.0)
    hh = max((y1 - y0) * 0.65, 1.0)
    return cx - hw, cy - hh, cx + hw, cy + hh


def bake_profile(profile, bbox: tuple, resolution: int = 256) -> int:
    """Evaluate profile on a 2D grid and upload as a GL r32f texture.
    Must be called inside an active GL context. Returns GL texture ID.
    Row 0 = y=y0 = UV.y=0 (no flip needed — matches OpenGL row-0=bottom convention).
    """
    x0, y0, x1, y1 = bbox
    xs = np.linspace(x0, x1, resolution, dtype=np.float32)
    ys = np.linspace(y0, y1, resolution, dtype=np.float32)
    xg, yg = np.meshgrid(xs, ys)            # shape (H, W): row=y, col=x
    pts = np.stack([xg.ravel(), yg.ravel()], axis=1).astype(np.float32)
    dist = profile.evaluate_2d_grid(pts).reshape(resolution, resolution).astype(np.float32)
    # Row 0 = y0; OpenGL puts row 0 at UV.y=0 (bottom) — correct mapping, no flip.
    return _upload_r32f_tex2d(dist)


def _upload_r32f_tex2d(data: np.ndarray) -> int:
    h, w = data.shape
    flat = data.flatten(order='C')
    ptr  = flat.ctypes.data_as(ctypes.c_void_p)

    glGenTextures   = _loader.get("glGenTextures",
        [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
    glBindTexture   = _loader.get("glBindTexture",
        [ctypes.c_uint, ctypes.c_uint], None)
    glTexImage2D    = _loader.get("glTexImage2D",
        [ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
         ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p], None)
    glTexParameteri = _loader.get("glTexParameteri",
        [ctypes.c_uint, ctypes.c_uint, ctypes.c_int], None)

    tid = ctypes.c_uint(0)
    glGenTextures(1, ctypes.byref(tid))
    glBindTexture(GL_TEXTURE_2D, tid.value)
    glTexImage2D(GL_TEXTURE_2D, 0, GL_R32F, w, h, 0, GL_RED, GL_FLOAT, ptr)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
    glBindTexture(GL_TEXTURE_2D, 0)
    return tid.value


def delete_texture(tex_id: int):
    if not tex_id:
        return
    glDeleteTextures = _loader.get("glDeleteTextures",
        [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
    if glDeleteTextures:
        tid = ctypes.c_uint(tex_id)
        glDeleteTextures(1, ctypes.byref(tid))
