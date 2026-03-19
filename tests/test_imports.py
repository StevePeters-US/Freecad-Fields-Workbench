import sys
print("STEP 1: sys imported", flush=True)
import os
print("STEP 2: os imported", flush=True)
import numpy as np
print("STEP 3: numpy imported", flush=True)

from types import ModuleType
fc = ModuleType("FreeCAD")
sys.modules["FreeCAD"] = fc
print("STEP 4: FreeCAD mocked", flush=True)

# Try importing the mesher
print("STEP 5: Importing SurfaceNetsMesher...", flush=True)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from core.dm_mesher import SurfaceNetsMesher
print("STEP 6: SurfaceNetsMesher imported", flush=True)
