# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Generate the preset matcap PNGs in this directory.

Run from anywhere:  python3 Resources/matcaps/generate_presets.py

The images come from `freecad/fields/core/render/matcap_bake.py`, which the workbench
also uses to bake material variants. Every image is 256x256 sRGB RGBA, a sphere seen
head-on, transparent black outside the disc. Needs only numpy: the PNG is written with
zlib directly so the script runs outside FreeCAD.

Never a single point light (the lighting presets integrate an analytic environment),
and never a downloaded image: third-party matcap packs carry their own terms, and this
repo is CC-BY-NC-SA-4.0.
"""
import importlib.util
import os
import struct
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
_BAKE = os.path.join(HERE, "..", "..", "freecad", "fields", "core", "render", "matcap_bake.py")

# Loaded by path: importing `freecad.fields` would pull in FreeCAD, and matcap_bake
# is deliberately numpy-only.
_spec = importlib.util.spec_from_file_location("matcap_bake", _BAKE)
matcap_bake = importlib.util.module_from_spec(_spec)
import sys  # noqa: E402
sys.modules["matcap_bake"] = matcap_bake       # dataclasses resolves its module by name
_spec.loader.exec_module(matcap_bake)

#: Written in layer order; MATCAP_NAMES in matcap_texture_cache.py is built the same way.
NAMES = (*matcap_bake.LIGHTING_NAMES, *matcap_bake.MATERIAL_NAMES, *matcap_bake.DATA_NAMES)


def _png(path, rgba8):
    h, w, _ = rgba8.shape
    raw = b"".join(b"\x00" + rgba8[y].tobytes() for y in range(h))

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(raw, 9)))
        f.write(chunk(b"IEND", b""))


def render(name):
    return matcap_bake.render(name)


def main():
    for name in NAMES:
        _png(os.path.join(HERE, name + ".png"), render(name))
        print("wrote", name + ".png")


if __name__ == "__main__":
    main()
