"""Create cell coverage wedges from an existing QGIS point layer."""
import math
from pathlib import Path

from qgis.core import (
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsDistanceArea,
    QgsFeature, QgsField, QgsGeometry, QgsMapLayerProxyModel, QgsPointXY,
    QgsProject, QgsSpatialIndex, QgsVectorFileWriter, QgsVectorLayer, QgsWkbTypes,
)
from qgis.gui import QgsFieldComboBox, QgsMapLayerComboBox
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QCheckBox, QColorDialog, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLineEdit, QMessageBox, QPushButton,
)


def create_cells(layer, site_field, azimuth_field, sector_field, beamwidth_field,
                 default_beamwidth, radius, reduce_overlap=False):
    project = QgsProject.instance()
    crs = QgsCoordinateReferenceSystem("EPSG:4326")
    if not layer.crs().isValid():
        raise ValueError("Assign the correct CRS to the input layer first.")
    transform = QgsCoordinateTransform(layer.crs(), crs, project)
    distance = QgsDistanceArea()
    distance.setSourceCrs(crs, project.transformContext())
    distance.setEllipsoid("WGS84")
    output = QgsVectorLayer("Polygon?crs=EPSG:4326", "Cell Make Azimuth", "memory")
    provider = output.dataProvider()
    provider.addAttributes([
        QgsField("site_id", QVariant.String), QgsField("sector", QVariant.String),
        QgsField("azimuth", QVariant.Double), QgsField("beamwidth", QVariant.Double),
        QgsField("radius_m", QVariant.Double),
    ])
    output.updateFields()
    seen = set()
    centers = {}
    for source in layer.getFeatures():
        try:
            if source.geometry().isEmpty() or source.geometry().isMultipart():
                raise ValueError("expected a single point")
            site = field_text(source, site_field)
            sectors = field_text(source, sector_field).split(",")
            azimuths = field_text(source, azimuth_field).split(",")
            if not site or len(sectors) != len(azimuths):
                raise ValueError("provide a SiteID and matching sector and azimuth values")
            beamwidths = field_text(source, beamwidth_field).split(",") if beamwidth_field else [""]
            if len(beamwidths) == 1:
                beamwidths *= len(sectors)
            if len(beamwidths) != len(sectors):
                raise ValueError("provide one beamwidth or one per sector")
            center = transform.transform(QgsPointXY(source.geometry().asPoint()))
            if abs(center.y()) >= 90:
                raise ValueError("site is at a geographic pole")
            cells = []
            centers[site] = center
            for sector, raw_azimuth, raw_width in zip(sectors, azimuths, beamwidths):
                sector = sector.strip()
                azimuth = float(raw_azimuth)
                width = float(raw_width) if raw_width.strip() else default_beamwidth
                if not sector or not 0 <= azimuth <= 360 or not 0 < width <= 360:
                    raise ValueError("enter a sector ID, azimuth 0–360°, and beamwidth greater than 0° up to 360°")
                key = (site, sector)
                if key in seen:
                    raise ValueError(f"duplicate site and sector combination {site}/{sector}")
                seen.add(key)
                steps = max(2, math.ceil(width / 3))
                ring = [
                    distance.computeSpheroidProject(
                        center, radius, math.radians(azimuth - width / 2 + width * step / steps)
                    )
                    for step in range(steps + 1)
                ]
                if any(abs(point.x() - center.x()) > 180 for point in ring):
                    raise ValueError("sectors crossing the antimeridian are unsupported")
                ring = ring[:-1] + [ring[0]] if width == 360 else [center, *ring, center]
                cell = QgsFeature(output.fields())
                cell.setGeometry(QgsGeometry.fromPolygonXY([ring]))
                cell.setAttributes([site, sector, azimuth % 360, width, radius])
                cells.append(cell)
            if not provider.addFeatures(cells)[0]:
                raise ValueError("could not write generated cells")
        except (ValueError, TypeError, OverflowError) as error:
            raise ValueError(f"Input feature {source.id()}: {error}") from error
    if not output.featureCount():
        raise ValueError("The input layer has no sites.")
    if reduce_overlap:
        features = {feature.id(): feature for feature in output.getFeatures()}
        index = QgsSpatialIndex(output.getFeatures())
        affected = set()
        for fid, feature in features.items():
            geometry = feature.geometry()
            for other_id in index.intersects(geometry.boundingBox()):
                other = features[other_id]
                if other_id <= fid or other["site_id"] == feature["site_id"]:
                    continue
                if geometry.intersection(other.geometry()).area() > 0:
                    affected.update((feature["site_id"], other["site_id"]))
        geometries = {}
        attributes = {}
        for fid, feature in features.items():
            if feature["site_id"] not in affected:
                continue
            center = centers[feature["site_id"]]
            width, azimuth = feature["beamwidth"], feature["azimuth"]
            steps = max(2, math.ceil(width / 3))
            ring = [
                distance.computeSpheroidProject(
                    center, radius / 2,
                    math.radians(azimuth - width / 2 + width * step / steps),
                )
                for step in range(steps + 1)
            ]
            ring = ring[:-1] + [ring[0]] if width == 360 else [center, *ring, center]
            geometries[fid] = QgsGeometry.fromPolygonXY([ring])
            attributes[fid] = {output.fields().indexFromName("radius_m"): radius / 2}
        if not provider.changeGeometryValues(geometries) or not provider.changeAttributeValues(attributes):
            raise ValueError("Could not update the reduced sector radii.")
    output.updateExtents()
    return output


def field_text(feature, field):
    value = feature[field]
    return "" if QgsVariantUtils.isNull(value) else str(value).strip()


# QGIS 3.22 precedes QgsVariantUtils.
try:
    from qgis.core import QgsVariantUtils
except ImportError:
    from qgis.core import NULL

    class QgsVariantUtils:
        @staticmethod
        def isNull(value):
            return value is None or value == NULL


class SiteToCellDialog(QDialog):
    def __init__(self, iface):
        super().__init__(iface.mainWindow())
        self.setWindowTitle("Site to Cell Converter (Cell Make Azimuth)")
        form = QFormLayout(self)
        self.layer = QgsMapLayerComboBox()
        self.layer.setFilters(QgsMapLayerProxyModel.PointLayer)
        form.addRow("Input point layer", self.layer)
        self.fields = []
        for label in ("SiteID field", "Azimuth field", "Sector field", "Beamwidth field (optional)"):
            combo = QgsFieldComboBox()
            combo.setAllowEmptyFieldName(True)
            form.addRow(label, combo)
            self.layer.layerChanged.connect(combo.setLayer)
            self.fields.append(combo)
        self.layer.layerChanged.connect(self.select_fields)
        active = iface.activeLayer()
        if active and hasattr(active, "wkbType") and QgsWkbTypes.geometryType(active.wkbType()) == QgsWkbTypes.PointGeometry:
            self.layer.setLayer(active)
        self.select_fields(self.layer.currentLayer())
        self.width = QDoubleSpinBox()
        self.width.setRange(0.01, 360)
        self.width.setValue(65)
        self.width.setSuffix("°")
        form.addRow("Default beamwidth", self.width)
        self.radius = QDoubleSpinBox()
        self.radius.setRange(0.01, 1000000)
        self.radius.setValue(200)
        self.radius.setSuffix(" m")
        form.addRow("Sector radius", self.radius)
        self.reduce_overlap = QCheckBox("Halve radius at sites with overlapping sectors")
        self.reduce_overlap.setChecked(True)
        self.reduce_overlap.setToolTip(
            "Checks overlap with other sites at the requested radius, then halves all sectors "
            "at affected sites once. Very close sites may still overlap."
        )
        form.addRow(self.reduce_overlap)
        self.color = QColor()
        self.color_button = QPushButton("Choose color…")
        self.color_button.clicked.connect(self.choose_color)
        form.addRow("Sector fill (optional)", self.color_button)
        self.path = QLineEdit()
        self.path.setPlaceholderText("Leave blank for a temporary layer")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.choose_path)
        output_row = QHBoxLayout()
        output_row.addWidget(self.path)
        output_row.addWidget(browse)
        form.addRow("GeoPackage (optional)", output_row)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Create Cells")
        buttons.accepted.connect(self.run)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def select_fields(self, layer):
        for combo, aliases in zip(self.fields, (
            ("siteid", "sitename", "site"), ("azimuth", "azim", "az"),
            ("sector", "sectorid", "cellid", "cellname"), ("beamwidth", "hpbw"),
        )):
            combo.setLayer(layer)
            fields = {field.name().lower().replace("_", "").replace(" ", ""): field.name()
                      for field in layer.fields()} if layer else {}
            combo.setField(next((fields[name] for name in aliases if name in fields), ""))

    def choose_color(self):
        color = QColorDialog.getColor(self.color, self, "Sector fill")
        if color.isValid():
            self.color = color
            self.color_button.setText(color.name())

    def choose_path(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save sectors", "", "GeoPackage (*.gpkg)")
        if path:
            self.path.setText(path)

    def run(self):
        layer = self.layer.currentLayer()
        fields = [combo.currentField() for combo in self.fields]
        try:
            if not layer or not all(fields[:3]):
                raise ValueError("Select an input point layer and map the SiteID, azimuth and sector fields.")
            output = create_cells(
                layer, *fields, self.width.value(), self.radius.value(),
                self.reduce_overlap.isChecked(),
            )
            path = self.path.text().strip()
            if path:
                output = self.save_geopackage(output, path)
            if self.color.isValid():
                output.renderer().symbol().setColor(self.color)
                if path:
                    message, saved = output.saveDefaultStyle()
                    if not saved:
                        QMessageBox.warning(self, "Sector style", message)
            QgsProject.instance().addMapLayer(output)
        except Exception as error:
            QMessageBox.warning(self, "Cell Make Azimuth", str(error))
            return
        self.accept()

    @staticmethod
    def save_geopackage(layer, path, layer_name="cell_sectors"):
        if not path.lower().endswith(".gpkg"):
            path += ".gpkg"
        if Path(path).exists():
            raise ValueError("Choose a new GeoPackage filename. The selected file already exists.")
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = layer_name
        result = QgsVectorFileWriter.writeAsVectorFormatV3(
            layer, path, QgsProject.instance().transformContext(), options
        )
        if result[0] != QgsVectorFileWriter.NoError:
            raise ValueError(result[1])
        output = QgsVectorLayer(f"{path}|layername={layer_name}", layer.name(), "ogr")
        if not output.isValid():
            raise ValueError("The GeoPackage was written but QGIS could not open the sector layer.")
        return output
