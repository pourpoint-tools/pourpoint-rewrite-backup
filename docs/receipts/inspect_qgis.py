import json
import sys
from pathlib import Path

from qgis.core import QgsApplication, QgsProject

app = QgsApplication([], False)
app.initQgis()
path = Path(sys.argv[1])
project = QgsProject.instance()
read = project.read(str(path))
rows = []
for layer in project.mapLayers().values():
    row = {
        "name": layer.name(),
        "valid": layer.isValid(),
        "source": layer.source(),
        "crs": layer.crs().authid(),
        "provider": layer.providerType(),
    }
    if hasattr(layer, "featureCount"):
        row["features"] = layer.featureCount()
    rows.append(row)
report = {
    "project": str(path),
    "read": read,
    "layers": rows,
    "groups": [g.name() for g in project.layerTreeRoot().findGroups()],
}
print(json.dumps(report, indent=2))
Path(sys.argv[2]).write_text(json.dumps(report, indent=2))
assert read and rows and all(row["valid"] for row in rows)
project.clear()
app.exitQgis()
