# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""freecad.fields.core.sdf.sdf.noise_node

One noise kernel, declared once and executed three ways.

A node is a passive declaration: it does not dispatch, does not fall back, and does not
parse parameters. `SdfNoiseField` owns all three of those -- see D-4 in the noise
unification task list (`todo_noise_unification.md`; see also the `fld_noise_nodes`
skill, which is where that decision lives permanently). Subclasses are never
instantiated -- the registry maps a display name to the class object itself.
"""


class SdfNoiseNode:
    """Base class for a hash-based noise kernel.

    Subclasses set every attribute below and implement both kernels. The three
    implementations -- `python_fn`, `numpy_fn`, `main_fn_code` -- must agree to within
    float32 rounding on the same inputs; `test_noise_cpu_gpu_hash_parity.py` and
    `test_noise_scalar_np_parity.py` are what hold them together.

    All three take FRAME coordinates: `(x, y, z)` is the sample point relative to the
    pattern Center, projected onto `(u_axis, w_axis, direction)`. A planar node ignores
    `z` -- that is what makes its pattern constant along the direction arrow.
    """

    #: Display name in the NoiseType combo box and in saved `NoiseType` properties.
    name = ""

    #: True if the kernel ignores the third frame coordinate (a 2D pattern extruded
    #: along `direction`). Read for the panel's display text and the node-editor hint
    #: ONLY -- it must never select a code path (see NU-004).
    planar = False

    #: Comment block shown in the formula box when this node is selected.
    display = ""

    #: Upper bound on |d(kernel)/d(position)| per unit of `amp * freq`. Feeds
    #: `SdfNoiseField._lip()`; too low and the sphere march steps through the surface.
    lip_factor = 1.0

    #: Upper bound on |kernel| per unit of `amp`. Feeds `_wave_peak()` and so the
    #: bounding box; too low and the field gets culled where it is still non-zero.
    peak_factor = 1.0

    #: `[(helper_fn_name, glsl_source), ...]` emitted before `main_fn_code`, in order.
    dep_helpers = ()

    #: Name of the GLSL entry point defined by `main_fn_code`.
    main_fn_name = ""

    #: GLSL source. The entry point MUST be
    #: `float <main_fn_name>(vec3 q, float amp, float freq)` -- `q` is already in frame
    #: coordinates, so a node never re-projects and never sees `u_ax`/`w_ax`.
    main_fn_code = ""

    @staticmethod
    def python_fn(x, y, z, amp, freq):
        """Scalar kernel. Returns a float."""
        raise NotImplementedError

    @staticmethod
    def numpy_fn(x, y, z, amp, freq):
        """Vectorized kernel. `x`/`y`/`z` are equal-shaped arrays; returns one array of
        that shape. No Python-level loop over points -- see the `fld_vectorized_sdf`
        skill."""
        raise NotImplementedError

    @classmethod
    def validate(cls):
        """Raise ValueError if the declaration is incomplete. Called by the registry at
        import time so a malformed node fails on `import freecad.fields`, not on the
        first frame that tries to draw it."""
        for attr in ("name", "display", "main_fn_name", "main_fn_code"):
            if not getattr(cls, attr, ""):
                raise ValueError(f"{cls.__name__}: {attr} is required")
        sig = f"float {cls.main_fn_name}(vec3 q, float amp, float freq)"
        if sig not in cls.main_fn_code:
            raise ValueError(
                f"{cls.__name__}: main_fn_code must define exactly `{sig}`; "
                f"every node takes the frame point and nothing else")
        if cls.lip_factor <= 0.0 or cls.peak_factor <= 0.0:
            raise ValueError(f"{cls.__name__}: lip_factor/peak_factor must be > 0")

