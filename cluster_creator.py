"""Create balanced site groups and dissolve their site Voronoi cells."""
from math import ceil

from qgis.core import (
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeature, QgsField,
    QgsGeometry, QgsMapLayerProxyModel, QgsPointXY, QgsProject, QgsRectangle,
    QgsSpatialIndex, QgsVectorLayer, QgsWkbTypes,
)
from qgis.gui import QgsMapLayerComboBox
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLineEdit, QMessageBox, QPushButton, QSpinBox,
)

from .site_to_cell import SiteToCellDialog


def partition(indices, points, capacities, tolerance):
    if len(capacities) == 1:
        return [indices]
    middle = len(capacities) // 2
    left, right = capacities[:middle], capacities[middle:]
    desired = round(len(indices) * sum(left) / sum(capacities))
    low = max(sum(left) - len(left) * tolerance, len(left),
              len(indices) - sum(right) - len(right) * tolerance)
    high = min(sum(left) + len(left) * tolerance, len(indices) - len(right),
               len(indices) - max(len(right), sum(right) - len(right) * tolerance))
    axes = [(lambda p: p.x()), (lambda p: p.y())]
    axis = max(axes, key=lambda coord: max(coord(points[i]) for i in indices)
               - min(coord(points[i]) for i in indices))
    ordered = sorted(indices, key=lambda i: (axis(points[i]), i))
    cut = max(low, min(high, desired))
    if tolerance:
        cut = max(range(max(low, cut - tolerance), min(high, cut + tolerance) + 1),
                  key=lambda n: (axis(points[ordered[n]]) - axis(points[ordered[n-1]]),
                                 -abs(n - desired), -n))
    return (partition(ordered[:cut], points, left, tolerance)
            + partition(ordered[cut:], points, right, tolerance))


def create_clusters(layer, boundary, by_size, requested, tolerance):
    project = QgsProject.instance()
    context = project.transformContext()
    if not layer.crs().isValid() or (boundary and not boundary.crs().isValid()):
        raise ValueError("Assign a valid CRS to each input layer.")
    features = sorted(layer.getFeatures(), key=lambda feature: feature.id())
    if not features:
        raise ValueError("The site layer is empty.")
    if any(f.geometry().isEmpty() or f.geometry().isMultipart() for f in features):
        raise ValueError("Each site row must contain one point.")
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    center = QgsCoordinateTransform(layer.crs(), wgs84, context).transform(layer.extent().center())
    metric = QgsCoordinateReferenceSystem.fromProj(
        f"+proj=aeqd +lat_0={center.y()} +lon_0={center.x()} +datum=WGS84 +units=m +no_defs"
    )
    transform = QgsCoordinateTransform(layer.crs(), metric, context)
    points = [transform.transform(QgsPointXY(f.geometry().asPoint())) for f in features]
    if len({(p.x(), p.y()) for p in points}) != len(points):
        raise ValueError("Use one row per site with distinct coordinates. Coincident sites cannot have separate boundaries.")
    count = ceil(len(points) / requested) if by_size else requested
    if count > len(points):
        raise ValueError("The cluster count cannot exceed the number of sites.")
    base, extra = divmod(len(points), count)
    capacities = [base + (i < extra) for i in range(count)]
    if by_size and any(abs(size - requested) > tolerance for size in capacities):
        raise ValueError(f"{len(points)} sites across {count} clusters require "
                         f"{base}–{max(capacities)} sites per cluster. Increase the adjustment or change the target.")
    if by_size:
        # Use the per-cluster target as the center of the allowed interval.
        capacities = [requested] * count
    groups = partition(list(range(len(points))), points, capacities, tolerance)
    multipoint = QgsGeometry.fromMultiPointXY(points)
    if boundary:
        clip_transform = QgsCoordinateTransform(boundary.crs(), metric, context)
        shapes = []
        for feature in boundary.getFeatures():
            shape = feature.geometry()
            if not shape.isEmpty():
                shape.transform(clip_transform)
                shapes.append(shape.makeValid() if not shape.isGeosValid() else shape)
        domain = QgsGeometry.unaryUnion(shapes)
        if domain.isEmpty() or domain.area() <= 0:
            raise ValueError("The boundary layer must contain a polygon with positive area.")
        outside = sum(not domain.intersects(QgsGeometry.fromPointXY(point)) for point in points)
        if outside:
            raise ValueError(f"{outside} sites fall outside the selected boundary.")
    else:
        extent = QgsRectangle(multipoint.boundingBox())
        extent.grow(max(100, 0.05 * max(extent.width(), extent.height())))
        domain = QgsGeometry.fromRect(extent)

    owner = {i: cluster for cluster, group in enumerate(groups) for i in group}
    cells = [[] for _ in groups]
    if count == 1:
        cells[0].append(domain)
    else:
        diagram = multipoint.voronoiDiagram(domain)
        index = QgsSpatialIndex()
        for i, point in enumerate(points):
            seed = QgsFeature(i)
            seed.setGeometry(QgsGeometry.fromPointXY(point))
            index.addFeature(seed)
        seen = set()
        for cell in diagram.asGeometryCollection():
            seeds = [i for i in index.intersects(cell.boundingBox())
                     if cell.contains(QgsGeometry.fromPointXY(points[i]))]
            if len(seeds) != 1 or seeds[0] in seen:
                raise ValueError("Could not identify the unique site for each Voronoi cell.")
            i = seeds[0]
            seen.add(i)
            cells[owner[i]].append(cell.intersection(domain))
        if len(seen) != len(points):
            raise ValueError("Could not construct a Voronoi cell for every site.")

    output = QgsVectorLayer("MultiPolygon", "Cluster Boundaries", "memory")
    output.setCrs(metric)
    provider = output.dataProvider()
    provider.addAttributes([QgsField("cluster_id", QVariant.Int), QgsField("site_count", QVariant.Int)])
    output.updateFields()
    rows = []
    for i, group in enumerate(groups):
        shape = QgsGeometry.unaryUnion(cells[i])
        if shape.wkbType() == QgsWkbTypes.GeometryCollection:
            shape.convertGeometryCollectionToSubclass(QgsWkbTypes.PolygonGeometry)
        if shape.isEmpty() or not shape.isGeosValid() or shape.area() <= 0:
            raise ValueError(f"Could not create a valid polygon for cluster {i + 1}.")
        shape.convertToMultiType()
        feature = QgsFeature(output.fields())
        feature.setAttributes([i + 1, len(group)])
        feature.setGeometry(shape)
        rows.append(feature)
    if not provider.addFeatures(rows)[0]:
        raise ValueError("Could not write cluster boundaries.")
    output.updateExtents()
    return output, groups


class ClusterCreatorDialog(SiteToCellDialog):
    def __init__(self, iface):
        QDialog.__init__(self, iface.mainWindow())
        self.setWindowTitle("Cluster Creator")
        form = QFormLayout(self)
        self.layer = QgsMapLayerComboBox()
        self.layer.setFilters(QgsMapLayerProxyModel.PointLayer)
        if iface.activeLayer():
            self.layer.setLayer(iface.activeLayer())
        form.addRow("Input layer (one point per site)", self.layer)
        self.boundary = QgsMapLayerComboBox()
        self.boundary.setFilters(QgsMapLayerProxyModel.PolygonLayer)
        self.boundary.setAllowEmptyLayer(True)
        self.boundary.setLayer(None)
        form.addRow("Clipping boundary (optional)", self.boundary)
        self.mode = QComboBox()
        self.mode.addItems(["Number of clusters", "Target sites per cluster"])
        form.addRow("Planning mode", self.mode)
        self.requested = QSpinBox()
        self.requested.setRange(1, 1000000)
        self.requested.setValue(15)
        form.addRow("Requested count", self.requested)
        self.tolerance = QSpinBox()
        self.tolerance.setRange(0, 1000000)
        self.tolerance.setToolTip("Maximum sites above or below the planned cluster size.")
        form.addRow("Site adjustment per cluster (±)", self.tolerance)
        self.path = QLineEdit()
        self.path.setPlaceholderText("Leave blank for a temporary polygon layer")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.choose_path)
        output = QHBoxLayout()
        output.addWidget(self.path)
        output.addWidget(browse)
        form.addRow("GeoPackage (optional)", output)
        self.buttons = buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Create Clusters")
        buttons.accepted.connect(self.run)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def run(self):
        try:
            layer = self.layer.currentLayer()
            if layer is None:
                raise ValueError("Select a site point layer.")
            output, groups = create_clusters(
                layer, self.boundary.currentLayer(), self.mode.currentIndex() == 1,
                self.requested.value(), self.tolerance.value(),
            )
            if self.path.text().strip():
                output = self.save_geopackage(output, self.path.text().strip(), "cluster_boundaries")
            QgsProject.instance().addMapLayer(output)
        except Exception as error:
            QMessageBox.warning(self, "Cluster Creator", str(error))
            return
        sizes = list(map(len, groups))
        QMessageBox.information(self, "Cluster Creator",
                                f"Created {len(groups)} clusters with {min(sizes)}–{max(sizes)} sites each.")
        self.accept()
