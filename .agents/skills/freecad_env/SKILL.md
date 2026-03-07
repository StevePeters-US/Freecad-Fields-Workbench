---
name: FreeCAD Environment Setup
description: How to import FreeCAD Python modules from the AppImage when running agent scripts or standalone tests.
---

# FreeCAD Environment Setup

FreeCAD is installed as an AppImage at:
```
/home/steve/Programs/Freecad/FreeCAD_weekly-2026.01.21-Linux-x86_64-py311.AppImage
```

Agents running Python scripts **cannot** `import FreeCAD` directly — the modules live inside the AppImage.

---

## Option A: Run Python *inside* the AppImage (Recommended)

The AppImage bundles its own Python 3.11. Run scripts with it directly:

```bash
APPIMAGE=/home/steve/Programs/Freecad/FreeCAD_weekly-2026.01.21-Linux-x86_64-py311.AppImage

# Extract AppImage to a temp dir (one-time, ~10s):
"$APPIMAGE" --appimage-extract-and-run python3 your_script.py

# OR mount the squashfs and use the bundled python:
"$APPIMAGE" --appimage-mount &
MOUNT=$(ls -d /tmp/.mount_FreeCA* 2>/dev/null | tail -1)
"$MOUNT/usr/bin/python3" your_script.py
fusermount -u "$MOUNT"
```

---

## Option B: Add AppImage libs to sys.path (for quick reads, no GUI)

Mount the AppImage, then prepend paths before importing:

```python
import sys, subprocess, os

APPIMAGE = "/home/steve/Programs/Freecad/FreeCAD_weekly-2026.01.21-Linux-x86_64-py311.AppImage"

# Mount
proc = subprocess.Popen([APPIMAGE, "--appimage-mount"], stdout=subprocess.PIPE)
mount_point = proc.stdout.readline().decode().strip()

# Add FreeCAD Python paths
sys.path.insert(0, f"{mount_point}/usr/lib")
sys.path.insert(0, f"{mount_point}/usr/lib/python3/dist-packages")
sys.path.insert(0, f"{mount_point}/usr/lib/freecad/lib")

import FreeCAD  # now works

# ... your code ...

proc.terminate()
```

---

## Option C: Stub-out FreeCAD for pure-logic tests (No AppImage needed)

For testing code that *uses* `FreeCAD.Vector` / `FreeCAD.Base.Placement` etc. without the GUI, use the stub at `test_proj.py` as a pattern — define a minimal `Vector` class locally and avoid importing the real module.

```python
# At top of test file
import sys
from types import ModuleType

# Minimal FreeCAD stub
fc = ModuleType("FreeCAD")
class _Vec:
    def __init__(self, x=0, y=0, z=0): self.x=x; self.y=y; self.z=z
fc.Vector = _Vec
sys.modules["FreeCAD"] = fc

# Now your DM code can be imported
from core.frep.sdf.box import MCBoxField  # works without real FreeCAD
```

---

## Recommended Pattern for Agent Scripts

1. **Pure math / SDF logic** → use Option C (stub), no AppImage needed.
2. **Shape generation / Part module** → use Option A (run inside AppImage Python).
3. **GUI / Coin3D / ViewProvider** → cannot be tested headlessly; write unit tests for the logic layer only and verify visually in FreeCAD.

---

## Checking Which Python the AppImage Uses

```bash
/home/steve/Programs/Freecad/FreeCAD_weekly-2026.01.21-Linux-x86_64-py311.AppImage \
  --appimage-extract-and-run python3 --version
# Should print: Python 3.11.x
```
