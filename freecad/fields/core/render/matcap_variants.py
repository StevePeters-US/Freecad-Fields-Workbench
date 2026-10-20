# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Material matcap variants, stored in the document.

A variant is a recipe `{"name", "base", "color": [r, g, b], "metallic", "roughness"}`
(all 0-1, the colour in sRGB; `base` supplies the environment and the dielectric
specular level), kept as a JSON list under `doc.Meta["FieldsMatcapVariants"]`. Recipes, not pixels: tiny,
deterministic and diff-able, and re-baking one takes well under a second
(`matcap_bake.render_material`).

Two measured traps (FreeCAD 1.1 and 26.3rc1, 2026-10-14):

* `doc.Meta` persists through save and reopen. Whether a bare write marks the
  document modified was measured both ways (2026-10-05: no; 2026-10-06: yes), so
  every write here marks the GUI document modified explicitly.
* `Meta` is outside the undo stack: adding or removing a variant is NOT undoable.
  The dialog says so; no undo is built for it.
"""
import json

import FreeCAD

from freecad.fields.core import fld_logger
from freecad.fields.core.render.matcap_bake import MATERIALS, DATA_NAMES

META_KEY = "FieldsMatcapVariants"

#: One error per (document, raw string) so a bad Meta does not spam every redraw.
_reported = set()


def _valid(entry):
    try:
        color = entry["color"]
        return (isinstance(entry["name"], str) and entry["name"]
                and entry["base"] in MATERIALS
                and len(color) == 3 and all(0.0 <= float(c) <= 1.0 for c in color)
                and 0.0 <= float(entry["metallic"]) <= 1.0
                and 0.0 <= float(entry["roughness"]) <= 1.0)
    except Exception:
        return False


def variants(doc):
    """The document's recipes; [] when absent.

    Malformed JSON, or entries that fail validation, are dropped with one
    `fld_logger.error` per document and string -- never an exception: this is read
    on the render path.
    """
    raw = _raw(doc)
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError("not a list")
    except Exception as e:
        _report(doc, raw, f"cannot parse {META_KEY}: {e}")
        return []
    good = [e for e in data if _valid(e)]
    if len(good) != len(data):
        _report(doc, raw, f"dropped {len(data) - len(good)} malformed matcap variant(s)")
    return [{"name": e["name"], "base": e["base"], "color": [float(c) for c in e["color"]],
             "metallic": float(e["metallic"]), "roughness": float(e["roughness"])}
            for e in good]


def _raw(doc):
    try:
        return doc.Meta.get(META_KEY, "")
    except Exception:
        return ""


def _report(doc, raw, msg):
    key = (doc.Name, raw)
    if key not in _reported:
        _reported.add(key)
        fld_logger.error(f"{doc.Name}: {msg}")


def _builtin_names():
    from freecad.fields.core.render.matcap_texture_cache import MATCAP_NAMES_WITH_NONE
    return set(MATCAP_NAMES_WITH_NONE)


def _write(doc, recipes):
    meta = dict(doc.Meta)          # copy, edit, assign back: in-place edits are not guaranteed
    meta[META_KEY] = json.dumps(recipes)
    doc.Meta = meta
    _mark_modified(doc)


def _mark_modified(doc):
    if not FreeCAD.GuiUp:
        return
    try:
        import FreeCADGui
        FreeCADGui.getDocument(doc.Name).Modified = True
    except Exception as e:
        fld_logger.warn(f"{doc.Name}: could not mark the document modified: {e}")


def default_name(base, color_srgb):
    """The name a new material gets until the user types one: "<base> #rrggbb"."""
    return "%s #%02x%02x%02x" % (base, *(round(c * 255) for c in color_srgb))


def _check_values(color, metallic, roughness):
    """The colour as a list of floats; ValueError unless every value is in 0..1."""
    color = [float(c) for c in color]
    if len(color) != 3 or not all(0.0 <= c <= 1.0 for c in color):
        raise ValueError(f"variant colour must be 3 values in 0..1, got {color!r}")
    for what, v in (("metallic", metallic), ("roughness", roughness)):
        if not 0.0 <= float(v) <= 1.0:
            raise ValueError(f"variant {what} must be in 0..1, got {v!r}")
    return color


def add_variant(doc, base, color, name=None, metallic=None, roughness=None):
    """Append a recipe and return its (unique) name.

    `base` must be a material: not a data layer and not another variant. `metallic`
    and `roughness` default to the base material's. A name equal to a built-in or an
    existing variant gets " 2", " 3"... Marks the GUI document modified.
    """
    if base in DATA_NAMES or base not in MATERIALS:
        raise ValueError(f"cannot make a variant of {base!r}: not a material")
    if metallic is None:
        metallic = MATERIALS[base].metallic
    if roughness is None:
        roughness = MATERIALS[base].roughness
    color = _check_values(color, metallic, roughness)
    recipes = variants(doc)
    taken = _builtin_names() | {r["name"] for r in recipes}
    wanted = name or default_name(base, color)
    unique, n = wanted, 2
    while unique in taken:
        unique = f"{wanted} {n}"
        n += 1
    recipes.append({"name": unique, "base": base, "color": color,
                    "metallic": float(metallic), "roughness": float(roughness)})
    _write(doc, recipes)
    return unique


def remove_variant(doc, name):
    """Remove the recipe; return its base. The caller reassigns objects using it."""
    recipes = variants(doc)
    for i, r in enumerate(recipes):
        if r["name"] == name:
            del recipes[i]
            _write(doc, recipes)
            return r["base"]
    raise ValueError(f"no variant named {name!r}")


def update_variant(doc, name, color, metallic, roughness):
    """Replace a variant's colour, metallic and roughness in place. Its name and base
    are kept. Raises ValueError for an unknown name or a value outside 0..1. Marks the
    GUI document modified."""
    color = _check_values(color, metallic, roughness)
    recipes = variants(doc)
    for r in recipes:
        if r["name"] == name:
            r.update(color=color, metallic=float(metallic), roughness=float(roughness))
            _write(doc, recipes)
            return
    raise ValueError(f"no variant named {name!r}")


def ensure_loaded(doc, cache=None):
    """Push doc's recipes into the renderer's matcap cache if they changed since last time.

    Cheap when unchanged: compares the raw Meta string. Reaches the cache through the
    renderer singleton (guarded for no GUI) unless one is passed.
    """
    if cache is None:
        if not FreeCAD.GuiUp:
            return
        from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
        cache = FldSceneVoxelRenderer.get_instance()._matcap_cache
    raw = _raw(doc)
    if cache.meta_raw.get(doc.Name) == raw:
        return
    cache.set_document_variants(doc.Name, variants(doc))
    cache.meta_raw[doc.Name] = raw
