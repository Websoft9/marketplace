"""Catalog-independent product records shared by cloud inventory adapters."""

from dataclasses import dataclass


class InventoryError(ValueError):
    """An explicitly sanitized inventory failure safe to display to the caller."""


@dataclass
class Product:
    code: str
    entity_id: str
    title: str
    summary: str = ""
    visibility: str = ""
    offer: str = ""
    alternate_code: str = ""
    url: str = ""
