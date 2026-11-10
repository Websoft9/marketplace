from __future__ import annotations

import json
from pathlib import Path
import urllib.error

import click

from .. import catalog_verify, contentful, contentful_management
from .. import marketplace
from ..marketplace import CATALOG_URL, OFFER_SOURCE, catalog_default_path


def _parse_app_ids(value: str | None) -> tuple[str, ...] | None:
    if value is None:
        return None
    ids = tuple(item.strip() for item in value.split(",") if item.strip())
    if len(ids) == 1 and ids[0].lower() == "all":
        return None
    return ids


def _progress(enabled: bool):
    def report(message: str) -> None:
        if enabled:
            click.echo(message, err=True)
    return report


def _merge_dataset(path: Path, dataset: dict) -> dict:
    """Merge pulled items into an existing catalog without reordering it."""
    if not path.exists():
        return dataset
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise click.ClickException(f"failed to read existing catalog {path}: {error}") from None
    if isinstance(existing, list):
        existing = {"items": existing}
    if not isinstance(existing, dict) or not isinstance(existing.get("items"), list):
        raise click.ClickException(f"existing catalog {path} is not a catalog dataset")
    for index, item in enumerate(existing["items"]):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise click.ClickException(
                f"existing catalog {path} item {index} has no string id"
            )
    pulled = dataset.get("items") or []
    pulled_by_id = {item["id"]: item for item in pulled if isinstance(item, dict) and item.get("id")}
    merged_items = [pulled_by_id.pop(item["id"], item) for item in existing["items"]]
    merged_items.extend(item for item in pulled if item.get("id") in pulled_by_id)
    merged = dict(existing)
    merged["items"] = merged_items
    merged["count"] = len(merged_items)
    for key in ("schemaVersion", "generatedAt", "source"):
        if key in dataset:
            merged[key] = dataset[key]
    merged["source"] = {**(existing.get("source") or {}), **(dataset.get("source") or {})}
    return merged


@click.group("catalog")
def catalog_command() -> None:
    """Read application metadata from Contentful."""


@catalog_command.command("pull")
@click.option("--space-id", envvar="CONTENTFUL_SPACE_ID", required=True,
              help="Contentful space id. Prefer the CONTENTFUL_SPACE_ID environment variable.")
@click.option("--token", envvar="CONTENTFUL_TOKEN", required=True,
              help="Contentful token. A CFPAT- Management token is also supported for pull; otherwise use a Delivery/Preview token.")
@click.option("--environment", envvar="CONTENTFUL_ENVIRONMENT", default="master", show_default=True)
@click.option("--content-type", default="product", show_default=True,
              help="Contentful content type id.")
@click.option("--locale", default="en-US", show_default=True,
              help="Contentful locale for the English fields.")
@click.option("--id-field", default="key", show_default=True,
              help="Contentful field containing the application id (for example wordpress).")
@click.option("--fields", "field_list", default="trademark,overview,keywords,usecases,relatedApps,summary,hasenterprise", show_default=True,
              help="Comma-separated Contentful field ids to include.")
@click.option("--catalog", "catalog_source", default=CATALOG_URL, show_default=True, metavar="SOURCE",
              help="product_en.json URL or local path; only these application ids are returned.")
@click.option("--offer", "offer_source", default=OFFER_SOURCE, show_default=True, metavar="SOURCE",
              help="Offer-codes directory or JSON file used to add marketplace offer codes.")
@click.option("--apps", "app_list", metavar="IDS",
              help="Comma-separated application ids to export, for example wordpress,odoo. Default: all catalog applications.")
@click.option("--api-url", envvar="CONTENTFUL_API_URL", default=contentful.DEFAULT_API_URL, show_default=True,
              help="Contentful Delivery/Preview API base URL.")
@click.option("--output", type=click.Path(dir_okay=False, path_type=Path), metavar="PATH",
              help="Write JSON here. Default: data/.work/catalog.json; use - for stdout.")
@click.option("--merge", is_flag=True,
              help="Merge pulled items into an existing output file, preserving other items and their order.")
@click.option("--page-size", type=click.IntRange(1, 1000), default=100, show_default=True)
@click.option("--include-depth", type=click.IntRange(0, 10), default=1, show_default=True,
              help="Contentful link resolution depth.")
@click.option("--timeout", type=float, default=30, show_default=True,
              help="HTTP timeout in seconds.")
@click.option("--progress/--no-progress", default=True, show_default=True,
              help="Print progress to stderr.")
@click.option("--raw", is_flag=True,
              help="Keep Contentful values as returned instead of resolving links/RichText.")
@click.option("--allow-missing", is_flag=True,
              help="Write available records and list target applications absent from Contentful.")
def pull_command(
    space_id: str,
    token: str,
    environment: str,
    content_type: str,
    locale: str,
    id_field: str,
    field_list: str,
    catalog_source: str,
    offer_source: str,
    app_list: str | None,
    api_url: str,
    output: Path | None,
    merge: bool,
    page_size: int,
    include_depth: int,
    timeout: float,
    progress: bool,
    raw: bool,
    allow_missing: bool,
) -> None:
    """Pull English Contentful fields for every application in product_en.json.

    The output is one JSON dataset containing only the target applications from
    product_en.json, in catalog order, with the application id and requested fields.
    The API token is sent only in the Authorization header and is never printed.
    """
    fields = tuple(dict.fromkeys(item.strip() for item in field_list.split(",") if item.strip()))
    if not fields:
        raise click.ClickException("--fields must contain at least one Contentful field id.")
    app_ids = _parse_app_ids(app_list)
    if app_ids is not None and not app_ids:
        raise click.ClickException("--apps must contain at least one application id.")
    reporter = _progress(progress)
    try:
        dataset = contentful.pull_dataset(
            token=token,
            space_id=space_id,
            environment=environment,
            content_type=content_type,
            locale=locale,
            catalog_source=catalog_source,
            app_ids=app_ids,
            fields=fields,
            id_field=id_field,
            api_url=api_url,
            page_size=page_size,
            include_depth=include_depth,
            timeout=timeout,
            raw=raw,
            allow_missing=allow_missing,
            progress=reporter,
        )
    except contentful.ContentfulError as error:
        raise click.ClickException(str(error)) from None

    try:
        codes = marketplace.offer_codes_by_app(offer_source, timeout)
    except (OSError, ValueError, urllib.error.URLError) as error:
        raise click.ClickException(f"failed to read offer source: {error}") from None
    for item in dataset["items"]:
        item["offerCodes"] = codes.get(item["id"], {})
    dataset.setdefault("source", {})["offer"] = offer_source

    output = output if output is not None else catalog_default_path()
    if str(output) == "-":
        if merge:
            raise click.ClickException("--merge requires a file output, not stdout.")
        click.echo(contentful.dumps_dataset(dataset) + "\n", nl=False)
        return
    if merge:
        dataset = _merge_dataset(output, dataset)
    text = contentful.dumps_dataset(dataset) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    if progress:
        click.echo(f"Wrote {dataset['count']} application(s) to {output}", err=True)


@catalog_command.command("push")
@click.option("--space-id", envvar="CONTENTFUL_SPACE_ID", required=True,
              help="Contentful space id. Prefer the CONTENTFUL_SPACE_ID environment variable.")
@click.option("--token", envvar="CONTENTFUL_TOKEN", required=True,
              help="Contentful Management API token (CFPAT-). Prefer the CONTENTFUL_TOKEN environment variable.")
@click.option("--input", "input_path", metavar="PATH",
              help="JSON dataset from catalog pull, a JSON array, or - for stdin. Default: data/.work/catalog.json.")
@click.option("--apps", "app_list", metavar="IDS",
              help="Comma-separated application ids to push. Default: every item in the input.")
@click.option("--fields", "field_list", metavar="FIELDS",
              help="Comma-separated fields to push; default every supported field.")
@click.option("--environment", envvar="CONTENTFUL_ENVIRONMENT", default="master", show_default=True)
@click.option("--content-type", default="product", show_default=True)
@click.option("--locale", default="en-US", show_default=True,
              help="Locale updated for localized fields.")
@click.option("--id-field", default="key", show_default=True,
              help="Contentful field containing the application id.")
@click.option("--api-url", envvar="CONTENTFUL_MANAGEMENT_API_URL", default=contentful_management.DEFAULT_API_URL,
              show_default=True, help="Contentful Management API base URL.")
@click.option("--page-size", type=click.IntRange(1, 1000), default=1000, show_default=True)
@click.option("--timeout", type=float, default=30, show_default=True)
@click.option("--dry-run", is_flag=True, help="Show what would change without writing Contentful.")
@click.option("--publish", is_flag=True, help="Publish each successfully updated entry.")
@click.option("--clear", is_flag=True,
              help="Write empty strings/arrays instead of skipping empty input values.")
@click.option("--allow-missing", is_flag=True,
              help="Skip input applications that are absent from Contentful.")
@click.option("--verbose", is_flag=True, help="Print per-application update details to stderr.")
@click.option("--progress/--no-progress", default=True, show_default=True,
              help="Print progress to stderr.")
def push_command(
    space_id: str,
    token: str,
    input_path: str,
    app_list: str | None,
    field_list: str | None,
    environment: str,
    content_type: str,
    locale: str,
    id_field: str,
    api_url: str,
    page_size: int,
    timeout: float,
    dry_run: bool,
    publish: bool,
    clear: bool,
    allow_missing: bool,
    verbose: bool,
    progress: bool,
) -> None:
    """Push repository/catalog changes to Contentful drafts (and optionally publish).

    Only trademark, overview, keywords, usecases, relatedApps, summary and
    hasenterprise are written, and --fields can restrict the push further.
    relatedApps Trademarks are resolved to linked
    product entries; non-store and duplicate names are dropped, and other linked
    fields are not pushed. Empty values are skipped unless --clear is used.
    Writes use the entry's current version, so concurrent Contentful edits fail
    instead of being overwritten.
    """
    app_ids = _parse_app_ids(app_list)
    if app_ids is not None and not app_ids:
        raise click.ClickException("--apps must contain at least one application id.")
    field_names = None if field_list is None else {
        item.strip() for item in field_list.split(",") if item.strip()
    }
    if field_list is not None and not field_names:
        raise click.ClickException("--fields must contain at least one field.")
    if field_names is not None:
        unknown_fields = field_names - set(contentful_management.ALLOWED_INPUT_FIELDS)
        if unknown_fields:
            raise click.ClickException(
                f"unsupported push field(s): {', '.join(sorted(unknown_fields))}"
            )
    reporter = _progress(progress)
    input_path = input_path or str(catalog_default_path())
    try:
        summary = contentful_management.push(
            token=token,
            space_id=space_id,
            environment=environment,
            input_path=input_path,
            content_type=content_type,
            locale=locale,
            id_field=id_field,
            api_url=api_url,
            app_ids=app_ids,
            page_size=page_size,
            timeout=timeout,
            dry_run=dry_run,
            publish=publish,
            clear=clear,
            allow_missing=allow_missing,
            verbose=verbose,
            progress=reporter,
            field_names=field_names,
        )
    except contentful.ContentfulError as error:
        raise click.ClickException(str(error)) from None

    label = "Would update" if dry_run else "Updated"
    click.echo(f"Selected applications: {summary['selected']}", err=True)
    click.echo(f"Matched in Contentful: {summary['matched']}", err=True)
    click.echo(f"{label}: {summary['updated']}", err=True)
    if publish and not dry_run:
        click.echo(f"Published: {summary['published']}", err=True)
    click.echo(f"Unchanged: {summary['unchanged']}", err=True)
    click.echo(f"Skipped empty fields: {summary['skippedEmpty']}", err=True)
    if summary["missing"]:
        click.echo(f"Missing in Contentful: {', '.join(summary['missing'])}", err=True)
    if summary["errors"]:
        for error in summary["errors"]:
            click.echo(f"ERROR: {error}", err=True)
        raise click.ClickException(f"{len(summary['errors'])} push error(s).")
    if dry_run:
        click.echo("Dry run: no Contentful changes were written.", err=True)


@catalog_command.command("verify")
@click.option("--input", "input_path", type=click.Path(dir_okay=False, path_type=Path),
              metavar="PATH", help="Catalog dataset to validate. Default: data/.work/catalog.json.")
@click.option("--apps", "app_list", metavar="IDS",
              help="Comma-separated application ids to validate. Default: every item.")
@click.option("--fields", "field_list", metavar="FIELDS",
              default=",".join(catalog_verify.GENERATED_FIELDS), show_default=True,
              help="Comma-separated fields to validate.")
@click.option("--baseline", type=click.Path(dir_okay=False, path_type=Path), metavar="PATH",
              help="Earlier catalog snapshot; fail if unselected items or fields changed.")
@click.option("--require-non-empty", is_flag=True,
              help="Treat empty keywords/usecases as errors; use after generation.")
@click.option("--json", "as_json", is_flag=True, help="Print the full result as JSON.")
@click.option("--verbose", is_flag=True, help="Show a status line for applications without issues.")
def verify_command(
    input_path: Path | None,
    app_list: str | None,
    field_list: str,
    baseline: Path | None,
    require_non_empty: bool,
    as_json: bool,
    verbose: bool,
) -> None:
    """Validate catalog field rules and optional change isolation.

    Checks keywords, usecases, relatedApps, overview, summary and
    hasenterprise for the selected applications. With --baseline, also checks
    that no other item or field changed.
    """
    path = input_path or catalog_default_path()
    try:
        data = catalog_verify.load_dataset(str(path))
        baseline_data = catalog_verify.load_dataset(str(baseline)) if baseline else None
    except ValueError as error:
        raise click.ClickException(str(error)) from None
    fields = tuple(item.strip() for item in field_list.split(",") if item.strip())
    if not fields:
        raise click.ClickException("--fields must contain at least one field.")
    app_ids = _parse_app_ids(app_list)
    if app_ids is not None and not app_ids:
        raise click.ClickException("--apps must contain at least one application id.")
    try:
        result = catalog_verify.verify(
            data, app_ids=app_ids, fields=fields, baseline=baseline_data,
            require_non_empty=require_non_empty,
        )
    except ValueError as error:
        raise click.ClickException(str(error)) from None

    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        issues = result["issues"]
        for app_id in result["selected"]:
            app_issues = [issue for issue in issues if issue["app"] == app_id]
            if app_issues:
                click.echo(f"{app_id}: {len(app_issues)} issue(s)")
                for issue in app_issues:
                    field = f" {issue['field']}" if issue["field"] else ""
                    click.echo(f"  {issue['severity']}{field}: {issue['message']}")
            elif verbose:
                click.echo(f"{app_id}: ok")
        for issue in issues:
            if not issue["app"]:
                field = f" {issue['field']}" if issue["field"] else ""
                click.echo(f"{issue['severity']}{field}: {issue['message']}")
        if not issues:
            click.echo(f"Validated {len(result['selected'])} application(s): ok")
        else:
            click.echo(
                f"Validated {len(result['selected'])} application(s): "
                f"{result['errors']} error(s), {result['warnings']} warning(s)"
            )
    if result["errors"]:
        raise click.ClickException(f"{result['errors']} catalog validation error(s).")
