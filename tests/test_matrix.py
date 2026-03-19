import FreeCAD
import numpy as np

pl = FreeCAD.Placement(FreeCAD.Vector(10, 20, 30), FreeCAD.Rotation(FreeCAD.Vector(0,0,1), 45))
m = pl.toMatrix()
m.invert()

inv_matrix = np.array([
    [m.A11, m.A12, m.A13, m.A14],
    [m.A21, m.A22, m.A23, m.A24],
    [m.A31, m.A32, m.A33, m.A34],
    [m.A41, m.A42, m.A43, m.A44]
], dtype=np.float32)

pt = FreeCAD.Vector(100, 200, 300)
local_fc = pl.inverse().multVec(pt)

pts = np.array([[100, 200, 300]], dtype=np.float32)
pts_hom = np.hstack((pts, np.ones((1, 1), dtype=np.float32)))
local_np = (pts_hom @ inv_matrix.T)[:, :3]

print(f"FC local: {local_fc.x, local_fc.y, local_fc.z}")
print(f"NP local: {local_np[0].tolist()}")
