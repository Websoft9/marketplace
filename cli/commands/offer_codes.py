from __future__ import annotations

import json
import urllib.error

import click

from .. import offer_codes, offer_listing
from ..marketplace import CATALOG_URL, OFFER_SOURCE


@click.group("offer-codes")
def offer_codes_command() -> None:
    """Read, validate and audit local marketplace offer codes."""


@offer_codes_command.command("verify")
@click.option("--offer", "--offer-source", "offer_source", default=str(OFFER_SOURCE),
              show_default=True, metavar="SOURCE",
              help="Offer-codes directory or JSON file.")
@click.option("--product-catalog", "--catalog", "catalog_source",
              default=CATALOG_URL, show_default=True, metavar="SOURCE",
              help="product_en.json URL or local JSON. This is the source of truth "
                   "for which applications exist.")
@click.option("--strict", is_flag=True,
              help="Treat warnings as errors, for example in CI.")
@click.option("--check-coverage/--no-check-coverage", "check_coverage", default=True,
              help="Warn about product_en.json applications without an offer entry. "
                   "Default: on.")
@click.option("--json", "as_json", is_flag=True, help="Print the full result as JSON.")
@click.option("--verbose", is_flag=True,
              help="Show a status line for entries without issues.")
def verify_command(
    offer_source: str,
    catalog_source: str,
    strict: bool,
    check_coverage: bool,
    as_json: bool,
    verbose: bool,
) -> None:
    """Validate the structure and cross-entry rules of offer codes.

    Checks required fields, platform keys, code arrays, id and code formats,
    duplicate ids and codes, and compares application ids with product_en.json.
    Structure and duplicate-id problems are errors; format drift, unknown
    platforms and differences from product_en.json are warnings.
    """
    try:
        documents = offer_codes.load_source(offer_source)
    except (OSError, ValueError, urllib.error.URLError) as error:
        raise click.ClickException(f"failed to read offer codes: {error}") from None

    notes: list[str] = []
    catalog_ids: set[str] | None = None
    catalog_path = catalog_source or CATALOG_URL
    try:
        catalog_ids = offer_codes.load_catalog_ids(catalog_path)
    except (OSError, ValueError, urllib.error.URLError) as error:
        notes.append(
            f"product catalog {catalog_path} is not readable ({error}); "
            "skipped application-id checks"
        )

    result = offer_codes.verify(
        documents,
        catalog_ids=catalog_ids,
        check_coverage=check_coverage,
    )
    result["productCatalog"] = catalog_path if catalog_ids is not None else None
    result["notes"] = notes

    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        click.echo(f"Offer sources: {len(documents)}")
        click.echo(f"Offer entries: {result['entries']}")
        issues = result["issues"]
        grouped: dict[str, list[dict[str, str]]] = {}
        global_issues: list[dict[str, str]] = []
        order: list[str] = []
        for issue in issues:
            app = issue["app"]
            if not app:
                global_issues.append(issue)
                continue
            if app not in grouped:
                grouped[app] = []
                order.append(app)
            grouped[app].append(issue)
        for app in order:
            click.echo(f"{app}: {len(grouped[app])} issue(s)")
            for issue in grouped[app]:
                field = f" {issue['field']}" if issue["field"] else ""
                click.echo(f"  {issue['severity']}{field}: {issue['message']}")
        for issue in global_issues:
            field = f" {issue['field']}" if issue["field"] else ""
            click.echo(f"{issue['severity']}{field}: {issue['message']}")
        if verbose and not issues:
            click.echo("No issues found.")
        for note in notes:
            click.echo(f"note: {note}")
        click.echo(
            f"Verified {result['entries']} entry(ies): "
            f"{result['errors']} error(s), {result['warnings']} warning(s)"
        )

    failing = result["errors"] > 0 or (strict and result["strictWarnings"] > 0)
    if failing:
        raise click.ClickException(
            f"{result['errors']} error(s), "
            f"{result['warnings']} warning(s) in offer codes."
        )


@offer_codes_command.command("listing-check")
@click.option("--offer", "--offer-source", "offer_source", default=str(OFFER_SOURCE),
              show_default=True, metavar="SOURCE",
              help="Offer-codes directory or JSON file.")
@click.option("--platform", "platform_list", default="all", show_default=True,
              metavar="PLATFORMS",
              help="Comma-separated platform keys, or all.")
@click.option("--limit", type=click.IntRange(0), default=0, show_default=True,
              help="Maximum codes per platform; 0 checks every code.")
@click.option("--strict", is_flag=True,
              help="Fail when any code is reported as not found.")
@click.option("--json", "as_json", is_flag=True, help="Print the full result as JSON.")
def listing_check_command(
    offer_source: str,
    platform_list: str,
    limit: int,
    strict: bool,
    as_json: bool,
) -> None:
    """Check listing existence and visibility through seller APIs.

    Reads credentials from the environment and asks each cloud's seller or
    catalog API whether the code still resolves and whether it is public. This
    does not test public URL reachability. A platform without an adapter or
    credentials is reported as unsupported or skipped; it is never guessed.
    """
    try:
        documents = offer_codes.load_source(offer_source)
    except (OSError, ValueError, urllib.error.URLError) as error:
        raise click.ClickException(f"failed to read offer codes: {error}") from None

    if platform_list.strip().lower() == "all":
        platforms = None
    else:
        platforms = [
            item.strip() for item in platform_list.split(",") if item.strip()
        ]
        if not platforms:
            raise click.ClickException("--platform must contain at least one platform.")

    try:
        result = offer_listing.check_listings(
            documents, platforms=platforms, limit=limit
        )
    except ValueError as error:
        raise click.ClickException(str(error)) from None

    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        for platform, report in result["platforms"].items():
            if report["status"] == "empty":
                click.echo(f"{platform}: no codes")
            elif report["status"] in {offer_listing.SKIPPED, offer_listing.UNSUPPORTED}:
                click.echo(f"{platform}: {report['status']} ({report.get('reason', '')})")
            elif report["status"] == offer_listing.ERROR:
                click.echo(f"{platform}: error ({report.get('reason', '')})")
            else:
                detail = ", ".join(
                    f"{name} {report[name]}"
                    for name in
                    (offer_listing.EXISTS, offer_listing.NOT_FOUND,
                     offer_listing.ERROR)
                    if report.get(name)
                )
                click.echo(f"{platform}: {report['checked']} checked ({detail or 'no problems'})")
        for item in result["results"]:
            owner = ", ".join(item["apps"]) or "no owner"
            if item["status"] == offer_listing.EXISTS:
                if item.get("public") is False:
                    click.echo(
                        f"  {item['platform']} {item['code']} ({owner}): "
                        f"exists but is not public ({item['visibility']})"
                    )
                continue
            if item["status"] in {offer_listing.SKIPPED, offer_listing.UNSUPPORTED}:
                continue
            click.echo(
                f"  {item['platform']} {item['code']} ({owner}): "
                f"{item['status']} - {item['detail']}"
            )
        summary = result["summary"]
        click.echo(
            "Summary: "
            f"{summary['checked']} checked, "
            f"{summary['exists']} exists, "
            f"{summary['not_found']} not found, "
            f"{summary['skipped']} skipped, "
            f"{summary['unsupported']} unsupported, "
            f"{summary['errors']} error(s), "
            f"{summary['not_public']} not public"
        )

    summary = result["summary"]
    if summary["errors"] > 0 or (strict and summary["notFound"] > 0):
        raise click.ClickException(
            f"{summary['errors']} error(s), {summary['not_found']} not-found code(s)."
        )
