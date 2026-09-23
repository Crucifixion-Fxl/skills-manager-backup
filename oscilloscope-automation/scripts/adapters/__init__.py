"""Oscilloscope model adapters."""

from .rigol_mho_dho5000 import RigolMho5104Adapter

ADAPTERS = (RigolMho5104Adapter(),)


def select_adapter(idn: str):
    for adapter in ADAPTERS:
        if adapter.matches(idn):
            return adapter
    return None
