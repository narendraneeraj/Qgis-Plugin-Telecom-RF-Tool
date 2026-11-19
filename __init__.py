def classFactory(iface):
    from .plugin import TelecomRfTool

    return TelecomRfTool(iface)
