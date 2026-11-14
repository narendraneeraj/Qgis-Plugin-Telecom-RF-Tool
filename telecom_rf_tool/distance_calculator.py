"""Find the closest or farthest other site sharing an attribute."""
from collections import defaultdict

from qgis.core import (
    QgsDistanceArea, QgsFeature, QgsField, QgsFields, QgsMemoryProviderUtils, QgsProject,
)
from qgis.gui import QgsFieldComboBox
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtWidgets import QDialogButtonBox, QMessageBox

from .cluster_creator import ClusterCreatorDialog
from .site_to_cell import field_text


def calculate_distances(layer, site_field, match_field, farthest):
    if not layer.crs().isValid():
        raise ValueError("Assign the correct CRS to the input layer first.")
    distance = QgsDistanceArea()
    distance.setSourceCrs(layer.crs(), QgsProject.instance().transformContext())
    distance.setEllipsoid("WGS84")
    records = []
    groups = defaultdict(dict)
    for feature in layer.getFeatures():
        site = field_text(feature, site_field)
        value = field_text(feature, match_field)
        geometry = feature.geometry()
        if not site or geometry.isEmpty() or geometry.isMultipart():
            raise ValueError(f"Feature {feature.id()}: supply a SiteID and one point.")
        point = geometry.asPoint()
        records.append((feature, site, value, point))
        if value:
            anchor = groups[value].setdefault(site, point)
            if distance.measureLine(anchor, point) > 1:
                raise ValueError(f"Site {site} has locations more than 1 m apart for value {value}.")
    if not records:
        raise ValueError("The input layer has no sites.")

    results = {}
    for value, sites in groups.items():
        locations = sorted(sites.items())
        for index, (site, point) in enumerate(locations):
            for other, other_point in locations[index + 1:]:
                km = distance.measureLine(point, other_point) / 1000
                for origin, target in ((site, other), (other, site)):
                    key = (value, origin)
                    score = (-km if farthest else km, target)
                    if key not in results or score < results[key][0]:
                        results[key] = (score, target, km)

    fields = QgsFields(layer.fields())
    used_names = {field.name().casefold() for field in fields}
    for base, kind in (
        ("matched_site", QVariant.String), ("distance_km", QVariant.Double),
        ("match_status", QVariant.String),
    ):
        name = base
        suffix = 2
        while name.casefold() in used_names:
            name = f"{base}_{suffix}"
            suffix += 1
        used_names.add(name.casefold())
        fields.append(QgsField(name, kind))
    name = "Farthest Same-Attribute Sites" if farthest else "Nearest Same-Attribute Sites"
    output = QgsMemoryProviderUtils.createMemoryLayer(name, fields, layer.wkbType(), layer.crs())
    rows = []
    matched = 0
    for feature, site, value, _point in records:
        match = results.get((value, site))
        if match:
            _, target, km = match
            status = "Matched"
            matched += 1
        else:
            target = km = None
            status = "No other site" if value else "Blank attribute"
        row = QgsFeature(fields)
        row.setGeometry(feature.geometry())
        row.setAttributes([*feature.attributes(), target, km, status])
        rows.append(row)
    if not output.dataProvider().addFeatures(rows)[0]:
        raise ValueError("Could not write distance results.")
    output.updateExtents()
    return output, matched


class DistanceCalculatorDialog(ClusterCreatorDialog):
    def __init__(self, iface, farthest):
        super().__init__(iface)
        self.farthest = farthest
        self.setWindowTitle("Far Distance Calculator" if farthest else "Nearest Distance Calculator")
        form = self.layout()
        for control in (self.boundary, self.mode, self.requested, self.tolerance):
            form.removeRow(control)
        form.labelForField(self.layer).setText("Input site or cell point layer")
        self.site = QgsFieldComboBox()
        self.attribute = QgsFieldComboBox()
        form.insertRow(1, "SiteID field", self.site)
        form.insertRow(2, "Match attribute (e.g. PCI)", self.attribute)
        self.layer.layerChanged.connect(self.map_fields)
        self.map_fields(self.layer.currentLayer())
        self.buttons.button(QDialogButtonBox.Ok).setText("Calculate Distance")

    def map_fields(self, layer):
        names = {field.name().lower().replace("_", "").replace(" ", ""): field.name()
                 for field in layer.fields()} if layer else {}
        for combo, aliases in ((self.site, ("siteid", "site", "sitename")),
                               (self.attribute, ("pci", "plannedpci", "rsi", "plannedrsi"))):
            combo.setAllowEmptyFieldName(True)
            combo.setLayer(layer)
            combo.setField(next((names[key] for key in aliases if key in names), ""))

    def run(self):
        try:
            layer = self.layer.currentLayer()
            site, attribute = self.site.currentField(), self.attribute.currentField()
            if not layer or not site or not attribute or site == attribute:
                raise ValueError("Select an input point layer and distinct SiteID and match fields.")
            output, matched = calculate_distances(layer, site, attribute, self.farthest)
            if self.path.text().strip():
                name = "farthest_matches" if self.farthest else "nearest_matches"
                output = self.save_geopackage(output, self.path.text().strip(), name)
            QgsProject.instance().addMapLayer(output)
        except Exception as error:
            QMessageBox.warning(self, self.windowTitle(), str(error))
            return
        QMessageBox.information(self, self.windowTitle(),
                                f"Matched {matched:,} of {output.featureCount():,} input rows.")
        self.accept()
