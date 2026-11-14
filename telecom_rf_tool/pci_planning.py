"""Plan LTE PCIs in stepped sequences, one sequence per site."""
from collections import Counter, defaultdict
from math import ceil

from qgis.core import (
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsDistanceArea,
    QgsFeature, QgsField, QgsGeometry, QgsMapLayerProxyModel, QgsPointXY, QgsProject,
    QgsVectorLayer,
)
from qgis.gui import QgsFieldComboBox, QgsMapLayerComboBox
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtWidgets import (
    QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
)

from .site_to_cell import SiteToCellDialog, field_text


def plan_sites(sites, distance, start, end, step, reuse_km, fixed=None):
    """Return site assignments, tested reuse thresholds and global usage."""
    if not 0 <= start <= end <= 503 or step < 1 or reuse_km < 0:
        raise ValueError("Enter a valid PCI range, step and reuse distance.")
    first = start if step == 1 else max(step, ((start + step - 1) // step) * step)
    pool = tuple(range(first, end + 1, step))
    if not pool:
        raise ValueError("The selected range and step produce no eligible PCIs.")
    usage = Counter(dict.fromkeys(pool, 0))
    fixed = fixed or {}
    assigned = {}
    used_by_site = [
        (site, sites[site][0], tuple(pcis.values()))
        for site, pcis in fixed.items()
    ]
    for pcis in fixed.values():
        usage.update(pcis.values())
    candidates_by_size = {}
    for site_id, (point, sectors) in sorted(sites.items()):
        ordered = sorted(sectors, key=sector_order)
        locked = fixed.get(site_id, {})
        missing = [sector for sector in ordered if sector not in locked]
        if not missing:
            assigned[site_id] = (locked, None)
            continue

        def candidates(size):
            if size not in candidates_by_size:
                candidates_by_size[size] = [
                    pool[index:index + size] for index in range(len(pool) - size + 1)
                ]
            return candidates_by_size[size]

        options = [
            tuple(pci for sector, pci in zip(ordered, series) if sector not in locked)
            for series in candidates(len(ordered))
            if all(series[index] == locked[sector]
                   for index, sector in enumerate(ordered) if sector in locked)
        ]
        if not options:
            options = [series for series in candidates(len(missing))
                       if set(series).isdisjoint(locked.values())]
        if not options:
            raise ValueError(f"Site {site_id}: no PCI series fits the blank sectors. Expand the range or adjust the step.")
        nearest = defaultdict(lambda: float("inf"))
        for previous_site, previous_point, previous_pcis in used_by_site:
            if previous_site == site_id:
                continue
            separation = distance.measureLine(point, previous_point) / 1000
            if separation < reuse_km:
                for pci in previous_pcis:
                    nearest[pci] = min(nearest[pci], separation)

        def score(series):
            closest = min(nearest[pci] for pci in series)
            relaxed_steps = 0
            if closest < reuse_km:
                # Treat an exact threshold match as sufficient reuse distance.
                relaxed_steps = ceil((reuse_km - closest) / 2)
            counts = [usage[pci] for pci in series]
            return relaxed_steps, sum(counts), max(counts), series[0]

        series = min(options, key=score)
        threshold = max(0, reuse_km - 2 * score(series)[0])
        assigned[site_id] = ({**locked, **dict(zip(missing, series))}, threshold)
        used_by_site.append((site_id, point, series))
        usage.update(series)
    return assigned, usage


def sector_order(sector):
    return (0, int(sector), sector) if sector.isdecimal() else (1, 0, sector.casefold(), sector)


def read_sites(layer, site_field, sector_field, value_field, label, maximum):
    project = QgsProject.instance()
    crs = QgsCoordinateReferenceSystem("EPSG:4326")
    if not layer.crs().isValid():
        raise ValueError("Assign the correct CRS to the input layer first.")
    transform = QgsCoordinateTransform(layer.crs(), crs, project)
    distance = QgsDistanceArea()
    distance.setSourceCrs(crs, project.transformContext())
    distance.setEllipsoid("WGS84")
    sites = {}
    fixed = defaultdict(dict)
    for feature in layer.getFeatures():
        site = field_text(feature, site_field)
        sectors = [value.strip() for value in field_text(feature, sector_field).split(",")]
        geometry = feature.geometry()
        if not site or not all(sectors) or geometry.isEmpty() or geometry.isMultipart():
            raise ValueError(f"Feature {feature.id()}: provide a site ID, sector IDs and a single point.")
        point = transform.transform(QgsPointXY(geometry.asPoint()))
        anchor, site_sectors = sites.setdefault(site, (point, {}))
        if distance.measureLine(anchor, point) > 1:
            raise ValueError(f"Site {site} has points more than 1 m apart.")
        raw_pcis = field_text(feature, value_field).split(",") if value_field else [""]
        if raw_pcis == [""]:
            raw_pcis *= len(sectors)
        if len(raw_pcis) != len(sectors):
            raise ValueError(f"Feature {feature.id()}: enter one {label} value or blank slot per sector.")
        for sector, raw_pci in zip(sectors, raw_pcis):
            raw_pci = raw_pci.strip()
            if raw_pci:
                if not raw_pci.isascii() or not raw_pci.isdecimal() or not 0 <= int(raw_pci) <= maximum:
                    raise ValueError(f"Feature {feature.id()}: existing {label} must be an integer from 0 to {maximum}.")
                fixed[site][sector] = int(raw_pci)
            if sector in site_sectors:
                raise ValueError(f"Duplicate sector {sector} at site {site}.")
            site_sectors[sector] = feature.attributes()
    if not sites:
        raise ValueError("The input layer has no sites.")
    return sites, fixed, distance


def build_plan(layer, site_field, sector_field, start, end, step, reuse, pci_field=None):
    sites, fixed, distance = read_sites(layer, site_field, sector_field, pci_field, "PCI", 503)
    assignments, usage = plan_sites(sites, distance, start, end, step, reuse, fixed)
    reuse_sites = defaultdict(set)
    for site, (pcis, _) in assignments.items():
        for pci in pcis.values():
            reuse_sites[pci].add(site)
    output = QgsVectorLayer("Point?crs=EPSG:4326", "LTE PCI Plan", "memory")
    provider = output.dataProvider()
    fields = list(layer.fields())
    used_names = {field.name().casefold() for field in fields}
    for name, kind in (
        ("plan_sector", QVariant.String), ("planned_pci", QVariant.Int),
        ("reuse_distance_km", QVariant.Double), ("pss_id", QVariant.Int), ("sss_group", QVariant.Int),
        ("reuse_km", QVariant.Double),
    ):
        base = name
        suffix = 2
        while name.casefold() in used_names:
            name = f"{base}_{suffix}"
            suffix += 1
        used_names.add(name.casefold())
        fields.append(QgsField(name, kind))
    if not provider.addAttributes(fields):
        raise ValueError("Could not create output attributes.")
    output.updateFields()
    rows = []
    for site, (pcis, threshold) in assignments.items():
        point, original_attributes = sites[site]

        for sector, pci in pcis.items():
            row = QgsFeature(output.fields())
            row.setGeometry(QgsGeometry.fromPointXY(point))
            nearest = min(
                (distance.measureLine(point, sites[other][0]) / 1000
                 for other in reuse_sites[pci] if other != site),
                default=None,
            )
            row.setAttributes([
                *original_attributes[sector], sector, pci, nearest, pci % 3, pci // 3,
                threshold if sector not in fixed.get(site, {}) else None,
            ])
            rows.append(row)
    if not provider.addFeatures(rows)[0]:
        raise ValueError("Could not write the PCI plan.")
    output.updateExtents()
    relaxed = sum(threshold is not None and threshold < reuse for _, threshold in assignments.values())
    preserved = sum(map(len, fixed.values()))
    return output, len(sites), usage, relaxed, preserved


class PciPlanningDialog(SiteToCellDialog):
    def __init__(self, iface):
        QDialog.__init__(self, iface.mainWindow())
        self.setWindowTitle("LTE PCI Planning")
        form = QFormLayout(self)
        self.layer = QgsMapLayerComboBox()
        self.layer.setFilters(QgsMapLayerProxyModel.PointLayer)
        form.addRow("Input point layer", self.layer)
        self.site = QgsFieldComboBox()
        self.sector = QgsFieldComboBox()
        form.addRow("SiteID field", self.site)
        form.addRow("Sector field", self.sector)
        self.pci = QgsFieldComboBox()
        form.addRow("Existing PCI field (optional)", self.pci)
        self.layer.layerChanged.connect(self.map_fields)
        if iface.activeLayer():
            self.layer.setLayer(iface.activeLayer())
        self.map_fields(self.layer.currentLayer())

        self.start = self.add_number(form, "Start PCI", 0, 503, 0)
        self.end = self.add_number(form, "End PCI", 0, 503, 503)
        self.step = self.add_number(form, "PCI step", 1, 503, 1)
        self.reuse = QDoubleSpinBox()
        self.reuse.setRange(0, 1000)
        self.reuse.setValue(10)
        self.reuse.setSuffix(" km")
        form.addRow("Target reuse distance", self.reuse)
        note = QLabel("Reuse relaxes by 2 km as needed. A step above 1 uses multiples of that step.")
        note.setWordWrap(True)
        form.addRow(note)

        self.path = QLineEdit()
        self.path.setPlaceholderText("Leave blank for a temporary map layer")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.choose_path)
        output = QHBoxLayout()
        output.addWidget(self.path)
        output.addWidget(browse)
        form.addRow("GeoPackage (optional)", output)
        self.buttons = buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Plan PCI")
        buttons.accepted.connect(self.run)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    @staticmethod
    def add_number(form, label, minimum, maximum, default):
        control = QSpinBox()
        control.setRange(minimum, maximum)
        control.setValue(default)
        form.addRow(label, control)
        return control

    def map_fields(self, layer):
        for combo, aliases in ((self.site, ("siteid", "site", "sitename")),
                               (self.sector, ("sector", "sectorid", "cellid", "cellname")),
                               (self.pci, ("pci", "existingpci", "plannedpci"))):
            combo.setLayer(layer)
            combo.setAllowEmptyFieldName(True)
            names = {field.name().lower().replace("_", ""): field.name()
                     for field in layer.fields()} if layer else {}
            combo.setField(next((names[key] for key in aliases if key in names), ""))

    def run(self):
        layer = self.layer.currentLayer()
        site = self.site.currentField()
        sector = self.sector.currentField()
        try:
            if not layer or not site or not sector or site == sector:
                raise ValueError("Select a point layer and distinct SiteID and sector fields.")
            output, sites, usage, relaxed, preserved = build_plan(
                layer, site, sector, self.start.value(), self.end.value(),
                self.step.value(), self.reuse.value(), self.pci.currentField(),
            )
            path = self.path.text().strip()
            if path:
                output = self.save_geopackage(output, path, "lte_pci_plan")
            QgsProject.instance().addMapLayer(output)
        except Exception as error:
            QMessageBox.warning(self, "LTE PCI Planning", str(error))
            return
        cells = sum(usage.values())
        QMessageBox.information(
            self, "LTE PCI Planning",
            f"Assigned {cells - preserved:,} blank PCIs and preserved {preserved:,} across {sites:,} sites.\n"
            f"PCIs in usage summary: {len(usage)}. Average assignments: {cells / len(usage):.2f}.\n"
            f"Assignments per PCI: {min(usage.values())}–{max(usage.values())}.\n"
            f"Sites needing relaxed reuse: {relaxed:,}.",
        )
        self.accept()
