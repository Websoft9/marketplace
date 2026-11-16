"""Synthetic offer data; tests must never depend on data/.work or cloud state."""


def odoo_item():
    return {
        "id": "odoo", "trademark": "Odoo",
        "overview": "Odoo is a modular business application suite.",
        "summary": "Modular ERP and business apps suite",
        "keywords": ["erp", "crm", "inventory"],
        "usecases": ["Sales", "Inventory"],
        "relatedApps": ["PostgreSQL", "pgAdmin"],
        "hasenterprise": True,
        "offerCodes": {"aws": ["test-odoo-sku"]},
    }
