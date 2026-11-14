"""Add XYZ tile layers to the current project."""
from urllib.parse import unquote

from qgis.core import QgsProject, QgsProviderRegistry, QgsRasterLayer

BASEMAPS = {
    "Google Maps (Roads)": "https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}",
    "Google Satellite": "https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}",
    "Google Hybrid": "https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}",
    "OpenStreetMap": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
}


def add_basemap(name):
    project = QgsProject.instance()
    registry = QgsProviderRegistry.instance()
    uri = registry.encodeUri("wms", {
        "type": "xyz", "url": BASEMAPS[name], "zmin": "0",
        "zmax": "19" if name == "OpenStreetMap" else "20",
    })
    for existing in project.mapLayers().values():
        if existing.providerType() != "wms":
            continue
        parts = registry.decodeUri("wms", existing.source())
        if parts.get("type") != "xyz" or unquote(parts.get("url", "")) != BASEMAPS[name]:
            continue
        if existing.source() != uri or not existing.isValid():
            existing.setDataSource(uri, existing.name(), "wms")
        if not existing.isValid():
            raise ValueError(f"QGIS could not initialize the {name} XYZ layer.")
        node = project.layerTreeRoot().findLayer(existing.id())
        if node:
            node.setItemVisibilityCheckedParentRecursive(True)
        return existing
    layer = QgsRasterLayer(uri, name, "wms")
    if not layer.isValid():
        raise ValueError(f"QGIS could not initialize the {name} XYZ layer.")
    attribution = "© OpenStreetMap contributors" if name == "OpenStreetMap" else "© Google"
    metadata = layer.metadata()
    metadata.setRights([attribution])
    layer.setMetadata(metadata)
    project.addMapLayer(layer, False)
    root = project.layerTreeRoot()
    root.addLayer(layer)
    # Preserve vector visibility even when the project uses a custom layer order.
    if root.hasCustomLayerOrder():
        order = [item for item in root.customLayerOrder() if item != layer]
        root.setCustomLayerOrder([*order, layer])
    return layer
