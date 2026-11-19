"""Distance-connected groups, balanced subdivisions and exclusive boundaries."""
import csv
from math import ceil
from pathlib import Path

from qgis.core import (
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsDistanceArea,
    QgsFeature, QgsField, QgsGeometry, QgsPointXY, QgsProject, QgsRectangle,
    QgsSpatialIndex, QgsVectorLayer, QgsVectorFileWriter,
)
from qgis.gui import QgsFieldComboBox
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLineEdit, QMessageBox, QPushButton, QSpinBox, QWidget,
)
from .cluster_creator import ClusterCreatorDialog as LegacyClusterDialog
from .site_to_cell import field_text


def build_clusters(layer, site_field, minimum=10, link_km=2, maximum=40,
                   adjustment=5, buffer_m=200):
    if minimum < 1 or maximum < minimum or link_km <= 0 or buffer_m <= 0:
        raise ValueError("Check the minimum, maximum, link distance and buffer.")
    project = QgsProject.instance()
    context = project.transformContext()
    if not layer.crs().isValid():
        raise ValueError("The input layer needs a valid CRS.")
    rows = sorted(layer.getFeatures(), key=lambda f: f.id())
    if not rows:
        raise ValueError("The input is empty.")
    wgs = QgsCoordinateReferenceSystem("EPSG:4326")
    ll = QgsCoordinateTransform(layer.crs(), wgs, context)
    records = []
    ids = set()
    for row in rows:
        site = field_text(row, site_field)
        geom = row.geometry()
        if not site or site in ids or geom.isEmpty() or geom.isMultipart():
            raise ValueError("Use one single point per unique, nonblank SiteID.")
        ids.add(site)
        records.append((site, ll.transform(QgsPointXY(geom.asPoint()))))
    center = records[0][1]
    metric = QgsCoordinateReferenceSystem.fromProj(
        f"+proj=aeqd +lat_0={center.y()} +lon_0={center.x()} +datum=WGS84 +units=m"
    )
    transform = QgsCoordinateTransform(wgs, metric, context)
    points = [transform.transform(point) for _, point in records]
    # Keep all SiteIDs for counts, while geometry uses one seed per location.
    unique_points = {}
    for i, point in enumerate(points):
        unique_points.setdefault((point.x(), point.y()), i)
    distance = QgsDistanceArea()
    distance.setSourceCrs(wgs, context)
    distance.setEllipsoid("WGS84")
    neighbors = [set() for _ in points]
    for i in range(len(points)):
        for j in range(i):
            if distance.measureLine(records[i][1], records[j][1]) <= link_km * 1000:
                neighbors[i].add(j)
                neighbors[j].add(i)
    components = []
    remaining = set(range(len(points)))
    while remaining:
        pending = [min(remaining)]
        remaining.remove(pending[0])
        component = []
        while pending:
            i = pending.pop()
            component.append(i)
            found = neighbors[i] & remaining
            remaining.difference_update(found)
            pending.extend(sorted(found))
        if len(component) >= minimum:
            components.append(component)
    components.sort(key=lambda group: min(records[i][0] for i in group))
    if not components:
        raise ValueError("No connected group meets the minimum site count.")
    groups = []
    names = []
    domains = []

    def split(indices, count, domain):
        if count == 1:
            return [(indices, domain)]
        left_count = count // 2
        desired = round(len(indices) * left_count / count)
        candidates = []
        for axis in (0, 1):
            coord = lambda i: points[i].x() if axis == 0 else points[i].y()
            ordered = sorted(indices, key=lambda i: (coord(i), records[i][0]))
            low = max(left_count, desired - adjustment)
            high = min(len(indices) - (count - left_count), desired + adjustment)
            for cut in range(low, high + 1):
                if coord(ordered[cut]) <= coord(ordered[cut - 1]):
                    continue
                # Each resulting average must respect the configured hard maximum.
                if ceil(cut / left_count) > maximum or ceil((len(indices)-cut)/(count-left_count)) > maximum:
                    continue
                candidates.append((abs(cut-desired), -(coord(ordered[cut])-coord(ordered[cut-1])),
                                   axis, cut, ordered))
        if not candidates:
            raise ValueError("Could not split this group within the size limits.")
        _, _, axis, cut, ordered = min(candidates)
        a, b = points[ordered[cut-1]], points[ordered[cut]]
        midpoint = (a.x()+b.x())/2 if axis == 0 else (a.y()+b.y())/2
        box = domain.boundingBox()
        left_box, right_box = QgsRectangle(box), QgsRectangle(box)
        if axis == 0:
            left_box.setXMaximum(midpoint)
            right_box.setXMinimum(midpoint)
        else:
            left_box.setYMaximum(midpoint)
            right_box.setYMinimum(midpoint)
        return (split(ordered[:cut], left_count, domain.intersection(QgsGeometry.fromRect(left_box)))
                + split(ordered[cut:], count-left_count, domain.intersection(QgsGeometry.fromRect(right_box))))

    for number, component in enumerate(components, 1):
        domain = QgsGeometry.fromMultiPointXY([points[i] for i in component]).convexHull().buffer(buffer_m, 16)
        count = ceil(len(component) / maximum)
        for child, (members, shape) in enumerate(split(component, count, domain), 1):
            groups.append(members)
            names.append(f"CL-{number:02}" + (f"-{child}" if count > 1 else ""))
            domains.append(shape)

    # Resolve collisions between independent group envelopes through nearest-site cells.
    occupied = QgsGeometry.unaryUnion(domains)
    seed_geom = QgsGeometry.fromMultiPointXY([points[i] for i in unique_points.values()])
    voronoi = seed_geom.voronoiDiagram(occupied)
    index = QgsSpatialIndex()
    for i in unique_points.values():
        point = points[i]
        f = QgsFeature(i)
        f.setGeometry(QgsGeometry.fromPointXY(point))
        index.addFeature(f)
    owner = {i: n for n, group in enumerate(groups) for i in group}
    tiles = [[] for _ in groups]
    collisions = any(a.intersection(b).area() > 1e-6
                     for i, a in enumerate(domains) for b in domains[:i])
    if collisions:
        for cell in voronoi.asGeometryCollection():
            seeds = [i for i in index.intersects(cell.boundingBox())
                     if cell.contains(QgsGeometry.fromPointXY(points[i]))]
            if len(seeds) != 1:
                raise ValueError("Could not resolve an exclusive boundary cell.")
            i = seeds[0]
            if i in owner:
                n = owner[i]
                tiles[n].append(cell.intersection(domains[n]))
        domains = [QgsGeometry.unaryUnion(parts) for parts in tiles]
    for n, domain in enumerate(domains):
        if domain.isEmpty() or not domain.isGeosValid() or domain.isMultipart():
            raise ValueError(f"{names[n]} cannot form one continuous boundary within these settings. Adjust the link distance or buffer.")
        if any(not domain.intersects(QgsGeometry.fromPointXY(points[i])) for i in groups[n]):
            raise ValueError(f"Boundary for {names[n]} does not contain all its sites.")
    if any(a.intersection(b).area() > 1e-5 for i, a in enumerate(domains) for b in domains[:i]):
        raise ValueError("Boundary overlap remains. Adjust the settings.")

    boundaries = QgsVectorLayer("Polygon", "Cluster Boundaries", "memory")
    boundaries.setCrs(metric)
    boundaries.dataProvider().addAttributes([
        QgsField("cluster_name", QVariant.String), QgsField("site_count", QVariant.Int)])
    boundaries.updateFields()
    site_output = QgsVectorLayer("Point", "Clustered Sites", "memory")
    site_output.setCrs(metric)
    site_output.dataProvider().addAttributes([
        QgsField("site_id", QVariant.String), QgsField("cluster_name", QVariant.String),
        QgsField("status", QVariant.String)])
    site_output.updateFields()
    features = []
    for n, domain in enumerate(domains):
        f = QgsFeature(boundaries.fields())
        f.setGeometry(domain)
        f.setAttributes([names[n], len(groups[n])])
        features.append(f)
    if not boundaries.dataProvider().addFeatures(features)[0]:
        raise ValueError("Could not write boundaries.")
    features = []
    for i, point in enumerate(points):
        n = owner.get(i)
        f = QgsFeature(site_output.fields())
        f.setGeometry(QgsGeometry.fromPointXY(point))
        f.setAttributes([records[i][0], names[n] if n is not None else None,
                         "Clustered" if n is not None else "Below minimum group size"])
        features.append(f)
    if not site_output.dataProvider().addFeatures(features)[0]:
        raise ValueError("Could not write clustered sites.")
    boundaries.updateExtents()
    site_output.updateExtents()
    return boundaries, site_output


class ClusterCreatorDialog(LegacyClusterDialog):
    def __init__(self, iface):
        QDialog.__init__(self, iface.mainWindow())
        self.setWindowTitle("Cluster Boundary Creator")
        form = QFormLayout(self)
        self.source = QComboBox()
        self.source.addItems(["QGIS layer", "Import CSV"])
        form.addRow("Input source", self.source)
        self.csv_path = QLineEdit()
        browse = QPushButton("Browse CSV…")
        browse.clicked.connect(self.open_csv)
        self.csv_row = QWidget()
        row = QHBoxLayout(self.csv_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.csv_path)
        row.addWidget(browse)
        form.addRow("CSV file", self.csv_row)
        from qgis.gui import QgsMapLayerComboBox
        from qgis.core import QgsMapLayerProxyModel
        self.layer = QgsMapLayerComboBox()
        self.layer.setFilters(QgsMapLayerProxyModel.PointLayer)
        if iface.activeLayer():
            self.layer.setLayer(iface.activeLayer())
        form.addRow("Input site point layer", self.layer)
        self.site = QgsFieldComboBox()
        self.site.setLayer(self.layer.currentLayer())
        self.layer.layerChanged.connect(self.site.setLayer)
        form.addRow("SiteID field", self.site)
        self.lat, self.lon = QComboBox(), QComboBox()
        form.addRow("CSV latitude field", self.lat)
        form.addRow("CSV longitude field", self.lon)
        self.csv_site = QComboBox()
        form.addRow("CSV SiteID field", self.csv_site)
        self.minimum = self.number(form, "Minimum connected sites", 10)
        self.link = self.decimal(form, "Inter-site link distance (km)", 2)
        self.buffer = self.decimal(form, "Boundary buffer (m)", 200)
        self.maximum = self.number(form, "Split above site count", 40)
        self.adjustment = self.number(form, "Split adjustment (± sites)", 5, 0)
        self.paths = []
        for label in ("Boundary GeoJSON (optional)", "Clustered-sites GeoJSON (optional)"):
            path = QLineEdit()
            button = QPushButton("Browse…")
            button.clicked.connect(lambda checked=False, target=path: self.choose_output(target))
            row = QHBoxLayout()
            row.addWidget(path)
            row.addWidget(button)
            form.addRow(label, row)
            self.paths.append(path)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Create Clusters")
        buttons.accepted.connect(self.run)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.source.currentIndexChanged.connect(self.update_source)
        self.update_source()

    def update_source(self):
        csv_selected = self.source.currentIndex() == 1
        form = self.layout()
        for control in (self.layer, self.site):
            control.setVisible(not csv_selected)
            control.setEnabled(not csv_selected)
            form.labelForField(control).setVisible(not csv_selected)
        for control in (self.csv_row, self.lat, self.lon, self.csv_site):
            control.setVisible(csv_selected)
            control.setEnabled(csv_selected)
            form.labelForField(control).setVisible(csv_selected)

    @staticmethod
    def number(form, label, default, minimum=1):
        field = QSpinBox()
        field.setRange(minimum, 1000000)
        field.setValue(default)
        form.addRow(label, field)
        return field

    @staticmethod
    def decimal(form, label, default):
        field = QDoubleSpinBox()
        field.setRange(.001, 1000000)
        field.setDecimals(3)
        field.setValue(default)
        form.addRow(label, field)
        return field

    def open_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select sites CSV", "", "CSV (*.csv)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8-sig", newline="") as handle:
                fields = next(csv.reader(handle))
            for combo in (self.lat, self.lon, self.csv_site):
                combo.clear()
                combo.addItems(fields)
            for combo, aliases in ((self.lat, ("latitude", "lat")), (self.lon, ("longitude", "lon", "long")),
                                   (self.csv_site, ("siteid", "site_id", "site"))):
                combo.setCurrentIndex(next((i for i, name in enumerate(fields) if name.lower() in aliases), 0))
            self.csv_path.setText(path)
        except (OSError, StopIteration) as error:
            QMessageBox.warning(self, "CSV", str(error))

    def choose_output(self, target):
        path, _ = QFileDialog.getSaveFileName(self, "Save output", "", "GeoJSON (*.geojson)")
        if path:
            target.setText(path if path.lower().endswith(".geojson") else path + ".geojson")

    def run(self):
        try:
            layer = self.layer.currentLayer()
            site = self.site.currentField()
            if self.source.currentIndex() == 1:
                if not self.csv_path.text().strip():
                    raise ValueError("Choose a CSV file.")
                if not all(combo.currentText() for combo in (self.lat, self.lon, self.csv_site)):
                    raise ValueError("Select latitude, longitude and SiteID CSV fields.")
                layer = QgsVectorLayer("Point?crs=EPSG:4326&field=site_id:string", "CSV Sites", "memory")
                site = "site_id"
                with open(self.csv_path.text().strip(), encoding="utf-8-sig", newline="") as handle:
                    for line, values in enumerate(csv.DictReader(handle), 2):
                        lat, lon = float(values[self.lat.currentText()]), float(values[self.lon.currentText()])
                        if not -90 < lat < 90 or not -180 <= lon <= 180:
                            raise ValueError(f"CSV row {line}: invalid latitude or longitude.")
                        f = QgsFeature(layer.fields())
                        f.setAttributes([values[self.csv_site.currentText()]])
                        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(lon, lat)))
                        if not layer.dataProvider().addFeatures([f])[0]:
                            raise ValueError(f"Could not read CSV row {line}.")
            if layer is None or not site:
                raise ValueError("Select a point layer and SiteID field or provide a CSV.")
            paths = [control.text().strip() for control in self.paths]
            if any(Path(path).exists() for path in paths if path):
                raise ValueError("Choose new output filenames.")
            if paths[0] and paths[0] == paths[1]:
                raise ValueError("Use different output filenames.")
            outputs = build_clusters(layer, site, self.minimum.value(), self.link.value(),
                                     self.maximum.value(), self.adjustment.value(), self.buffer.value())
            for output, path in zip(outputs, paths):
                if path:
                    options = QgsVectorFileWriter.SaveVectorOptions()
                    options.driverName = "GeoJSON"
                    options.ct = QgsCoordinateTransform(output.crs(), QgsCoordinateReferenceSystem("EPSG:4326"),
                                                        QgsProject.instance().transformContext())
                    result = QgsVectorFileWriter.writeAsVectorFormatV3(
                        output, path, QgsProject.instance().transformContext(), options)
                    if result[0] != QgsVectorFileWriter.NoError:
                        raise ValueError(result[1])
                QgsProject.instance().addMapLayer(output)
        except Exception as error:
            QMessageBox.warning(self, "Cluster Creator", str(error))
            return
        QMessageBox.information(self, "Cluster Creator",
                                f"Created {outputs[0].featureCount()} cluster boundaries. Check site status for excluded groups.")
        self.accept()
