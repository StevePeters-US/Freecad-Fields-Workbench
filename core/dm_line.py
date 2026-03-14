import FreeCAD
try:
    from pivy import coin
except ImportError:
    coin = None

class DMLineSet:
    """Manages a set of Coin3D lines for visual feedback."""
    def __init__(self, parent_node, color=(1.0, 1.0, 1.0), width=1.0, pattern=0xFFFF):
        self._parent = parent_node
        self._sep = None
        self._coords = None
        self._lines = None
        self._style = None
        
        if coin and parent_node:
            self._sep = coin.SoSeparator()
            
            mat = coin.SoMaterial()
            mat.diffuseColor.setValue(*color)
            self._sep.addChild(mat)
            
            self._style = coin.SoDrawStyle()
            self._style.lineWidth = width
            self._style.linePattern = pattern
            self._sep.addChild(self._style)
            
            self._coords = coin.SoCoordinate3()
            self._sep.addChild(self._coords)
            
            self._lines = coin.SoLineSet()
            self._sep.addChild(self._lines)
            
            self._parent.addChild(self._sep)

    def update_lines(self, points, segments=None):
        """
        Update the line geometry.
        points: list of FreeCAD.Vectors or (x,y,z) tuples.
        segments: list of vertex counts per polyline (e.g., [2, 2] for two separate segments).
                 If None, treats all points as a single continuous polyline.
        """
        if not self._coords or not self._lines:
            return
            
        if not points:
            self._coords.point.setNum(0)
            self._lines.numVertices.setNum(0)
            return

        # Convert to Coin format
        coin_pts = []
        for p in points:
            if hasattr(p, "x"):
                coin_pts.append((p.x, p.y, p.z))
            else:
                coin_pts.append(p)
        
        self._coords.point.setValues(coin_pts)
        
        if segments:
            self._lines.numVertices.setValues(segments)
        else:
            self._lines.numVertices.setValues([len(points)])

    def set_visible(self, visible):
        """Not directly supported by SoSeparator without a switch, but we can manage via parent."""
        if not self._sep or not self._parent:
            return
        if visible:
            if self._parent.findChild(self._sep) < 0:
                self._parent.addChild(self._sep)
        else:
            if self._parent.findChild(self._sep) >= 0:
                self._parent.removeChild(self._sep)

    def undraw(self):
        """Cleanup."""
        if self._parent and self._sep:
            try:
                self._parent.removeChild(self._sep)
            except:
                pass
        self._sep = None
        self._coords = None
        self._lines = None
        self._parent = None
