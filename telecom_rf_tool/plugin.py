from pathlib import Path

from .basemaps import BASEMAPS, add_basemap

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QMessageBox

try:
    from qgis.PyQt.QtGui import QAction
except ImportError:  # Qt 5 exposes QAction in QtWidgets.
    from qgis.PyQt.QtWidgets import QAction


TOOLS = (
    "Site to Cell Converter",
    "Best NBR Creator",
    "NBR Selector",
    "PCI Planning",
    "RSI Planning",
    "Cluster Creator",
    "Cluster Generator",
    "Far Distance Calculator",
    "Nearest Distance Calculator",
)
BRANDING = (
    'Developed by <a href="https://easycalculator.org/">EasyCalculator.org</a>'
    '<br>Contact us at <a href="mailto:care@easycalculator.org">'
    'care@easycalculator.org</a>'
)


class TelecomRfTool:
    def __init__(self, iface):
        self.iface = iface
        self.menu = None
        self.icon = QIcon(str(Path(__file__).with_name("icon.svg")))

    def initGui(self):
        self.menu = self.iface.pluginMenu().addMenu(self.icon, "Telecom RF Tool")
        for title in TOOLS:
            action = QAction(self.icon, title, self.menu)
            action.triggered.connect(
                lambda checked=False, tool_title=title: self.show_tool(tool_title)
            )
            self.menu.addAction(action)
        self.menu.addSeparator()
        basemaps = self.menu.addMenu(self.icon, "Basemaps")
        for name in BASEMAPS:
            action = QAction(self.icon, name, basemaps)
            action.triggered.connect(lambda checked=False, name=name: self.load_basemap(name))
            basemaps.addAction(action)
        about = QAction(self.icon, "About & Contact", self.menu)
        about.triggered.connect(self.show_about)
        self.menu.addAction(about)

    def unload(self):
        if self.menu is not None:
            self.iface.pluginMenu().removeAction(self.menu.menuAction())
            self.menu.deleteLater()
            self.menu = None

    def show_tool(self, title):
        if title == TOOLS[0]:
            from .site_to_cell import SiteToCellDialog

            dialog = SiteToCellDialog(self.iface)
            dialog.exec()
            dialog.deleteLater()
            return
        if title == "PCI Planning":
            from .pci_planning import PciPlanningDialog

            dialog = PciPlanningDialog(self.iface)
            dialog.exec()
            dialog.deleteLater()
            return
        if title == "RSI Planning":
            from .rsi_planning import RsiPlanningDialog

            dialog = RsiPlanningDialog(self.iface)
            dialog.exec()
            dialog.deleteLater()
            return
        if title == "Cluster Generator":
            from .vendor_generator.cluster_generator import ClusterGenerator

            generator = ClusterGenerator(self.iface)
            try:
                generator.run()
            finally:
                if generator.dlg is not None:
                    generator.dlg.deleteLater()
            return
        if title == "Cluster Creator":
            from .cluster_creator import ClusterCreatorDialog

            dialog = ClusterCreatorDialog(self.iface)
            dialog.setWindowTitle(title)
            dialog.exec()
            dialog.deleteLater()
            return
        if title in ("Far Distance Calculator", "Nearest Distance Calculator"):
            from .distance_calculator import DistanceCalculatorDialog

            dialog = DistanceCalculatorDialog(self.iface, title == "Far Distance Calculator")
            dialog.exec()
            dialog.deleteLater()
            return
        self.show_dialog(title, "This tool is planned for a later release.")

    def load_basemap(self, name):
        try:
            add_basemap(name)
        except ValueError as error:
            QMessageBox.warning(self.iface.mainWindow(), "Basemaps", str(error))

    def show_about(self):
        self.show_dialog("About Telecom RF Tool", "Version 0.7.3 · Same-Attribute Distance Calculators")

    def show_dialog(self, title, message):
        dialog = QMessageBox(self.iface.mainWindow())
        dialog.setWindowTitle(title)
        dialog.setWindowIcon(self.icon)
        dialog.setIconPixmap(self.icon.pixmap(48, 48))
        dialog.setText(message)
        dialog.setInformativeText(BRANDING)
        dialog.setStandardButtons(QMessageBox.StandardButton.Close)
        dialog.exec()
        dialog.deleteLater()
