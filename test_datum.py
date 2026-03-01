import FreeCAD
import Part
doc = FreeCAD.newDocument()
print("Available App objects:", [x for x in dir(FreeCAD) if 'Datum' in x])

try:
    dp = doc.addObject("App::Part::DatumPlane", "DatumPlane")
    print("Created App::Part::DatumPlane")
except Exception as e:
    print("Error App::Part::DatumPlane:", e)

try:
    dp2 = doc.addObject("Part::DatumPlane", "DatumPlane2")
    print("Created Part::DatumPlane")
except Exception as e:
    print("Error Part::DatumPlane:", e)

try:
    dp3 = doc.addObject("PartDesign::Plane", "DatumPlane3")
    print("Created PartDesign::Plane")
except Exception as e:
    print("Error PartDesign::Plane:", e)
