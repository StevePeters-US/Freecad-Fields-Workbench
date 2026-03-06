import FreeCAD
import numpy as np
from core.frep.frep_field import FRepField

class SdfField(FRepField):
    """
    Base class for F-Rep fields defined by closed-form SDF formulas.
    Subclasses must implement:
    - evaluate(point: FreeCAD.Vector) -> float
    - evaluate_grid(points: np.ndarray) -> np.ndarray
    - bounding_box() -> tuple[FreeCAD.Vector, FreeCAD.Vector]
    """
    pass
