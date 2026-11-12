from __future__ import annotations

import urllib.error

import click

from ..marketplace import (
    CATALOG_URL,
    OFFER_SOURCE,
    collect_rows,
    load_json,
    read_offers,
    render_markdown,
)


@click.command("list")
@click.option("--offer", default=str(OFFER_SOURCE), metavar="SOURCE",
              help="Offer-codes directory or JSON file")
@click.option(
    "--catalog",
    default=CATALOG_URL,
    metavar="SOURCE",
    help="product_en.json URL or local path",
)
@click.option(
    "--timeout",
    type=float,
    default=30,
    show_default=True,
    help="HTTP timeout in seconds",
)
def list_command(
    offer: str,
    catalog: str,
    timeout: float,
) -> None:
    """List applications and their Marketplace product links."""
    try:
        offers = read_offers(offer, timeout)
        catalog_data = load_json(catalog, timeout)
    except (OSError, ValueError, urllib.error.URLError) as error:
        raise click.ClickException(f"failed to load marketplace data: {error}") from error

    if not isinstance(offers, list) or not isinstance(catalog_data, list):
        raise click.ClickException("offer and catalog data must be JSON arrays")

    rows = collect_rows(offers, catalog_data)
    click.echo(render_markdown(rows))
