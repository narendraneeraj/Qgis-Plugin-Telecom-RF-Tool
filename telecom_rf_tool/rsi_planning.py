"""Plan individual LTE PRACH root sequence indices for blank cells."""
from collections import Counter, defaultdict
from math import inf

from qgis.core import QgsFeature, QgsField, QgsGeometry, QgsProject, QgsVectorLayer
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtWidgets import QDialogButtonBox, QLabel, QMessageBox

from .pci_planning import PciPlanningDialog, read_sites, sector_order


def assign_rsi(sites, fixed, distance, start, end, target, gap):
    if not 0 <= start <= end <= 838 or target < 2 or gap < 0:
        raise ValueError("Enter a valid RSI range, reuse distance and same-site gap.")
    pool = range(start, end + 1)
    usage = Counter(dict.fromkeys(pool, 0))
    users = defaultdict(set)
    planned = {site: dict(fixed.get(site, {})) for site in sites}
    thresholds = {}
    for site, values in fixed.items():
        for rsi in values.values():
            usage[rsi] += 1
            users[rsi].add(site)

    reuse_levels = [target]
    while reuse_levels[-1] > 2:
        reuse_levels.append(max(2, round(reuse_levels[-1] - 2, 2)))

    for site, (point, sectors) in sorted(sites.items()):
        values = planned[site]
        blanks = sorted(sectors.keys() - values.keys(), key=sector_order)
        if not blanks:
            continue
        separations = {}

        def separation(other):
            if other not in separations:
                separations[other] = distance.measureLine(point, sites[other][0]) / 1000
            return separations[other]

        nearest = {
            rsi: min((separation(other) for other in users[rsi] if other != site), default=inf)
            for rsi in pool
        }
        for sector in blanks:
            allowed = [
                rsi for rsi in pool
                if all(abs(rsi - existing) > gap for existing in values.values())
            ]
            for threshold in reuse_levels:
                suitable = [rsi for rsi in allowed if nearest[rsi] >= threshold]
                if suitable:
                    rsi = min(suitable, key=lambda candidate: (
                        usage[candidate], -nearest[candidate], candidate
                    ))
                    values[sector] = rsi
                    thresholds[site, sector] = threshold
                    usage[rsi] += 1
                    users[rsi].add(site)
                    break
    return planned, thresholds, usage, users


def build_rsi_plan(layer, site_field, sector_field, rsi_field, start, end, target, gap):
    sites, fixed, distance = read_sites(layer, site_field, sector_field, rsi_field, "RSI", 838)
    planned, thresholds, usage, users = assign_rsi(sites, fixed, distance, start, end, target, gap)
    output = QgsVectorLayer("Point?crs=EPSG:4326", "LTE RSI Plan", "memory")
    provider = output.dataProvider()
    fields = list(layer.fields())
    names = {field.name().casefold() for field in fields}
    for base, kind in (
        ("rsi_plan_sector", QVariant.String), ("planned_rsi", QVariant.Int),
        ("rsi_reuse_distance_km", QVariant.Double), ("rsi_threshold_km", QVariant.Double),
        ("rsi_status", QVariant.String),
    ):
        name = base
        suffix = 2
        while name.casefold() in names:
            name = f"{base}_{suffix}"
            suffix += 1
        names.add(name.casefold())
        fields.append(QgsField(name, kind))
    if not provider.addAttributes(fields):
        raise ValueError("Could not create RSI output attributes.")
    output.updateFields()
    rows = []
    preserved = 0
    for site, (point, sectors) in sites.items():
        for sector, original in sectors.items():
            rsi = planned[site].get(sector)
            threshold = thresholds.get((site, sector))
            nearest = None
            status = "Unassigned"
            if rsi is not None:
                nearest = min(
                    (distance.measureLine(point, sites[other][0]) / 1000
                     for other in users[rsi] if other != site),
                    default=None,
                )
                status = "Assigned" if threshold is not None else "Preserved"
                preserved += status == "Preserved"
            row = QgsFeature(output.fields())
            row.setGeometry(QgsGeometry.fromPointXY(point))
            row.setAttributes([*original, sector, rsi, nearest, threshold, status])
            rows.append(row)
    if not provider.addFeatures(rows)[0]:
        raise ValueError("Could not write the RSI plan.")
    output.updateExtents()
    counts = [usage[rsi] for rsi in range(start, end + 1)]
    relaxed = sum(threshold < target for threshold in thresholds.values())
    return output, preserved, len(thresholds), relaxed, counts


class RsiPlanningDialog(PciPlanningDialog):
    def __init__(self, iface):
        super().__init__(iface)
        self.setWindowTitle("LTE RSI Planning")
        form = self.layout()
        form.labelForField(self.pci).setText("Existing RSI field (optional)")
        for control in (self.start, self.end):
            control.setMaximum(838)
        self.end.setValue(837)
        form.labelForField(self.start).setText("Start RSI")
        form.labelForField(self.end).setText("End RSI")
        self.step.setRange(0, 838)
        self.step.setValue(9)
        form.labelForField(self.step).setText("Same-site exclusion ± RSI")
        self.reuse.setMinimum(2)
        form.labelForField(self.reuse).setText("Target reuse distance")
        for label in self.findChildren(QLabel):
            if label.text().startswith("Reuse relaxes"):
                label.setText("Reuse relaxes in 2 km steps to a 2 km floor. LTE standard range is 0–837.")
        self.buttons.button(QDialogButtonBox.Ok).setText("Plan RSI")

    def map_fields(self, layer):
        super().map_fields(layer)
        names = {field.name().lower().replace("_", "").replace(" ", ""): field.name()
                 for field in layer.fields()} if layer else {}
        self.pci.setField(next((names[key] for key in (
            "rsi", "existingrsi", "plannedrsi", "rootsequenceindex"
        ) if key in names), ""))

    def run(self):
        layer = self.layer.currentLayer()
        site, sector, rsi = (field.currentField() for field in (self.site, self.sector, self.pci))
        try:
            if not layer or not site or not sector or site == sector or rsi in (site, sector):
                raise ValueError("Select a point layer and distinct SiteID, sector and optional RSI fields.")
            output, preserved, assigned, relaxed, counts = build_rsi_plan(
                layer, site, sector, rsi, self.start.value(), self.end.value(),
                self.reuse.value(), self.step.value(),
            )
            path = self.path.text().strip()
            if path:
                output = self.save_geopackage(output, path, "lte_rsi_plan")
            QgsProject.instance().addMapLayer(output)
        except Exception as error:
            QMessageBox.warning(self, "LTE RSI Planning", str(error))
            return
        QMessageBox.information(
            self, "LTE RSI Planning",
            f"Assigned: {assigned:,} | Preserved: {preserved:,}\n"
            f"Unassigned: {output.featureCount() - assigned - preserved:,} | Relaxed reuse: {relaxed:,}\n"
            f"Assignments per RSI in range: {min(counts)}–{max(counts)} "
            f"(average {sum(counts) / len(counts):.2f}).",
        )
        self.accept()
