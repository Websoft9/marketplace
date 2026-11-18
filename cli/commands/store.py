from __future__ import annotations

import json

import click

from .. import store


@click.group("store")
def store_command():
    """Query live marketplace store inventories independently of application mappings."""


@store_command.command("products")
@click.option("--platform", "platforms", required=True, multiple=True,
              type=click.Choice([*store.PLATFORMS, "all"]),
              help="Store platform; repeat to select several. all means implemented adapters.")
@click.option("--profile", default=None, help="AWS read-only seller profile (only when AWS is selected).")
@click.option("--visibility", type=click.Choice(store.VISIBILITIES), default="public", show_default=True,
              help="Explicit query scope; nonpublic inventories are not supported.")
@click.option("--json", "as_json", is_flag=True,
              help="Emit one JSON result to stdout; progress goes to stderr, including on failure.")
@click.pass_context
def products_command(ctx, platforms, profile, visibility, as_json):
    """List public image products, including products with no application mapping.

    Read-only: never reads/writes a matrix, resolves applications or updates listings.
    """
    if "all" in platforms and len(platforms) != 1:
        raise click.UsageError("--platform all cannot be combined with other selections")
    selected = store.PLATFORMS if platforms == ("all",) else platforms
    if profile is not None and "aws" not in selected:
        raise click.UsageError("--profile is only supported when AWS is selected")
    report = store.query_products(
        selected, profile=profile, visibility=visibility, progress=lambda message: click.echo(message, err=True),
    )
    if as_json:
        click.echo(json.dumps(report, indent=2, ensure_ascii=False))
        for result in report["results"]:
            for error in result["errors"]:
                click.echo(f"{result['platform']}: {error['message']}", err=True)
    else:
        for result in report["results"]:
            state = "complete" if result["complete"] else "FAILED (unverified)"
            click.echo(f"{result['platform']} / {result['store']['provider']}: {result['count']} products, {state}")
            click.echo("| Product code | Title | Short description |")
            click.echo("| --- | --- | --- |")
            for product in result["products"]:
                cells = [str(product[k]).replace("|", "\\|").replace("\r", " ").replace("\n", " ")
                         for k in ("code", "title", "shortDescription")]
                click.echo("| " + " | ".join(cells) + " |")
            for error in result["errors"]:
                click.echo(f"{result['platform']}: {error['message']}", err=True)
    if not report["complete"]:
        ctx.exit(1)
