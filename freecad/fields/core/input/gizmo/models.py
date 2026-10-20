# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""The universal gizmo's data models (`universal_gizmo` skill, reference/models.md).

Qt-free and Coin-free, so the controller and its maths run headless.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Protocol

import FreeCAD

AXES = ("x", "y", "z")


class Kind(Enum):
    TRANSLATE = "translate"   # arrow: one axis
    PLANE = "plane"           # square: two axes
    ROTATE = "rotate"         # ring: one axis
    SCALE = "scale"           # cube: one axis, or all three (uniform)


_AXIS_COUNTS = {Kind.TRANSLATE: (1, 3), Kind.ROTATE: (1,), Kind.PLANE: (2,), Kind.SCALE: (1, 3)}
_STEP_KIND = {Kind.TRANSLATE: "grid", Kind.PLANE: "grid", Kind.ROTATE: "angle", Kind.SCALE: "scale"}
_NAME_PREFIX = {Kind.TRANSLATE: "", Kind.PLANE: "plane_", Kind.ROTATE: "rot_", Kind.SCALE: "scale_"}


@dataclass(frozen=True)
class HandleId:
    """A handle: its kind plus the frame axes it acts on, e.g. (PLANE, ("x", "y"))."""
    kind: Kind
    axes: tuple

    def __post_init__(self):
        if (tuple(sorted(self.axes)) != tuple(self.axes) or len(set(self.axes)) != len(self.axes)
                or not set(self.axes) <= set(AXES)):
            raise ValueError(f"axes must be a sorted subset of x/y/z, got {self.axes!r}")
        if len(self.axes) not in _AXIS_COUNTS[self.kind]:
            raise ValueError(f"{self.kind.name} cannot act on {self.axes!r}")

    @property
    def uniform(self):
        """True for the centre cube, which scales all three axes together."""
        return self.kind is Kind.SCALE and len(self.axes) == 3

    @property
    def step_kind(self):
        """The snap step this handle uses: "grid", "angle" or "scale"."""
        return _STEP_KIND[self.kind]

    @property
    def name(self):
        """The drawable's name: 'x', 'plane_xy', 'rot_z', 'scale_y', 'scale_xyz'."""
        return _NAME_PREFIX[self.kind] + "".join(self.axes)

    @staticmethod
    def from_name(name):
        """Inverse of `name`."""
        for kind in (Kind.PLANE, Kind.ROTATE, Kind.SCALE):
            prefix = _NAME_PREFIX[kind]
            if name.startswith(prefix):
                return HandleId(kind, tuple(name[len(prefix):]))
        return HandleId(Kind.TRANSLATE, tuple(name))


def handle_set(move="", planes=(), rotate="", scale="", uniform_scale=False, free_move=False):
    """The handles a target offers (its `gizmo_handles()`); only these are drawn, hit and
    pressable. `move`, `rotate` and `scale` name axes ("xyz", "z", "" for none); `planes`
    names plane squares ("xy", "xz", "yz"). `uniform_scale=True` offers uniform scale
    about the pivot (`scale_xyz`); `free_move=True` offers free move (`xyz`). Nothing is on
    unless named:

        handle_set(move="xyz", planes=("xy", "xz", "yz"), rotate="xyz")    # no scale
        handle_set(move="xy", planes=("xy",), rotate="z", scale="xy")      # a work plane

    An unknown axis, or a plane not written in sorted order ("yx"), raises ValueError
    (`HandleId`)."""
    hs = [HandleId(Kind.TRANSLATE, (a,)) for a in move]
    hs += [HandleId(Kind.PLANE, tuple(p)) for p in planes]
    hs += [HandleId(Kind.ROTATE, (a,)) for a in rotate]
    hs += [HandleId(Kind.SCALE, (a,)) for a in scale]
    if uniform_scale:
        hs.append(HandleId(Kind.SCALE, AXES))
    if free_move:
        hs.append(HandleId(Kind.TRANSLATE, AXES))
    return frozenset(hs)


ALL_HANDLES = handle_set(move="xyz", planes=("xy", "xz", "yz"), rotate="xyz", scale="xyz",
                         uniform_scale=True, free_move=True)


def world_axes():
    """Global X/Y/Z as an axes dict."""
    return {"x": FreeCAD.Vector(1, 0, 0), "y": FreeCAD.Vector(0, 1, 0), "z": FreeCAD.Vector(0, 0, 1)}


@dataclass
class GizmoFrame:
    """Where the gizmo sits. The target supplies it fresh on every redraw."""
    origin: FreeCAD.Vector         # arrows, planes and scale cubes are drawn here
    local_axes: dict               # "x"/"y"/"z" -> unit vector; orthonormal, right-handed
    pivot: FreeCAD.Vector          # rotation and scale centre


@dataclass
class GizmoDelta:
    """The TOTAL transform since the press, never an increment. Fields outside the
    handle's kind keep their identity values."""
    handle: HandleId
    pivot: FreeCAD.Vector          # frozen at the press
    axes: dict                     # frozen at the press, already resolved for the space
    translation: FreeCAD.Vector = field(default_factory=FreeCAD.Vector)
    rotation_axis: Optional[FreeCAD.Vector] = None
    rotation_deg: float = 0.0      # signed and unwrapped: may exceed +-180
    scale: dict = field(default_factory=lambda: {"x": 1.0, "y": 1.0, "z": 1.0})
    typed: bool = False
    snapped: bool = False


@dataclass
class SnapSettings:
    grid_step: float      # mm; 0 turns grid snap off
    angle_step: float     # degrees; 0 turns angle snap off
    scale_step: float     # factor increment; 0 turns scale snap off
    vertex: bool          # snap translations to vertex candidates
    pixel_radius: float   # vertex capture radius, screen px


class Phase(Enum):
    IDLE = 0
    DRAG = 1
    TYPED = 2             # typed entry; the mouse drives only the untyped components (D-52)


@dataclass
class DragState:
    phase: Phase = Phase.IDLE
    handle: Optional[HandleId] = None
    snapshot: object = None
    press_pos: object = None
    anchor: Optional[FreeCAD.Vector] = None   # world point under the press, on the constraint
    frozen: Optional[GizmoFrame] = None       # local_axes already resolved for the space
    press_angle: float = 0.0                  # ROTATE: ring angle at the press, radians
    last_angle: float = 0.0                   # ROTATE: unwrapped total so far, radians
    length: float = 1.0                       # gizmo length L frozen at the press (axis SCALE)
    mouse_delta: Optional[GizmoDelta] = None  # the mouse's last measured (snapped) delta; held edge-on, drives untyped components (D-52)
    view_dir: Optional[FreeCAD.Vector] = None   # free move: the press ray's direction (D-57)
    typed: Optional["TypedEntry"] = None       # TYPED: the buffers being edited


@dataclass
class TypedEntry:
    """Typed entry in progress: one buffer per component; index is the one Tab is on."""
    handle: HandleId
    buffers: list
    index: int = 0


class GizmoTarget(Protocol):
    """Implemented by a tool. See reference/models.md for the contract."""
    def gizmo_frame(self) -> Optional[GizmoFrame]: ...
    def gizmo_handles(self) -> frozenset: ...
    def gizmo_snapshot(self) -> object: ...
    def gizmo_apply(self, snapshot: object, delta: GizmoDelta) -> None: ...
    def gizmo_restore(self, snapshot: object) -> None: ...
    def gizmo_commit(self) -> None: ...
    def gizmo_snap_points(self) -> list: ...


class GizmoHost(Protocol):
    """Provided by the workbench (FldGizmoHost). See reference/models.md."""
    def ray(self, pos): ...
    def mouse_pos(self): ...
    def to_screen(self, p): ...
    def handle_scale(self, at) -> float: ...
    def ctrl_held(self) -> bool: ...
    def shift_held(self) -> bool: ...
    def snap_settings(self) -> SnapSettings: ...
    def step_snap(self, kind: str, notches: int) -> float: ...
    def vertex_candidates(self, near) -> list: ...
    def scene_root(self): ...
    def status(self, text) -> None: ...
    def readout(self, text) -> None: ...   # HUD strip, bottom of the view (D-53); None hides
    def open_transaction(self, name: str) -> None: ...
    def commit_transaction(self) -> None: ...
    def abort_transaction(self) -> None: ...
    def redraw(self) -> None: ...
