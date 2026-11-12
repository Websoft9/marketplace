from __future__ import annotations

import difflib
import json
import urllib.error
from functools import wraps
from pathlib import Path

import click

from .. import offer_codes, offer_listing
from ..marketplace import CATALOG_URL, OFFER_SOURCE


@click.group("offer-codes")
def offer_codes_command() -> None:
    """Read, validate, audit and maintain local marketplace offer codes."""


def _load_local(offer_source: str, *, validate: bool = False) -> list[tuple[str, object]]:
    try:
        documents = offer_codes.load_source(offer_source)
    except (OSError, ValueError, urllib.error.URLError) as error:
        raise click.ClickException(f"failed to read offer codes: {error}") from None
    for source, _document in documents:
        if not Path(source).is_file():
            raise click.ClickException(
                f"this command needs a local offer source; {source} is not a file"
            )
    if validate:
        try:
            offer_codes.require_structure(documents)
        except ValueError as error:
            raise click.ClickException(str(error)) from None
    return documents


def _load_catalog(
    catalog_source: str, skip_check: bool = False
) -> tuple[set[str] | None, list[str]]:
    if skip_check:
        return None, ["target-application check explicitly skipped (--skip-catalog-check)"]
    catalog_path = catalog_source or CATALOG_URL
    try:
        return offer_codes.load_catalog_ids(catalog_path), []
    except (OSError, ValueError, urllib.error.URLError) as error:
        raise click.ClickException(
            f"cannot validate application catalog {catalog_path}; nothing written. "
            "Use a readable --product-catalog or explicitly --skip-catalog-check. "
            f"({type(error).__name__})"
        ) from None


def _locked_edit(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            with offer_codes.source_lock(kwargs["offer_source"]):
                return function(*args, **kwargs)
        except (OSError, ValueError) as error:
            raise click.ClickException(str(error)) from None
    return wrapped


def _snapshot(documents):
    original = {
        source: Path(source).read_text(encoding="utf-8") for source, _ in documents
    }
    for source, document in documents:
        if json.loads(original[source]) != document:
            raise click.ClickException("offer source changed while reading; retry the command")
    return original


def _ensure_in_catalog(
    app: str,
    resolved: str | None,
    catalog_ids: set[str] | None,
    catalog_source: str,
) -> None:
    names = {app.strip()}
    if resolved:
        names.add(resolved)
    if catalog_ids is None or names & catalog_ids:
        return
    target = app.strip()
    if resolved and resolved != target:
        target = f"{target} (entry {resolved})"
    raise click.ClickException(
        f"application {target!r} is not present in {catalog_source}"
    )


def _new_issues(
    baseline: dict, result: dict, severity: str
) -> list[dict[str, str]]:
    seen = {
        _issue_key(issue)
        for issue in baseline["issues"]
        if issue["severity"] == severity
    }
    return [
        issue
        for issue in result["issues"]
        if issue["severity"] == severity and _issue_key(issue) not in seen
    ]


def _new_errors(baseline: dict, result: dict) -> list[dict[str, str]]:
    return _new_issues(baseline, result, "error")


def _print_new_warnings(issues: list[dict[str, str]]) -> None:
    for issue in issues:
        subject = issue["app"] or "dataset"
        field = f" {issue['field']}" if issue["field"] else ""
        click.echo(f"new warning {subject}{field}: {issue['message']}")


def _app_label(plan: dict) -> str:
    requested = plan.get("requestedApp")
    if requested and requested != plan["app"]:
        return f"{requested} (entry {plan['app']})"
    return plan["app"]


def _changes(
    documents: list[tuple[str, object]], original: dict[str, str], sources: list[str]
) -> str:
    wanted = set(sources)
    return "".join(
        _document_diff(source, original[source], document)
        for source, document in documents
        if source in wanted
    )


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
@click.option("--skip-catalog-check", is_flag=True,
              help="Run only local structural checks, without fetching a product catalog.")
@click.option("--json", "as_json", is_flag=True, help="Print the full result as JSON.")
@click.option("--verbose", is_flag=True,
              help="Show a status line for entries without issues.")
def verify_command(
    offer_source: str,
    catalog_source: str,
    strict: bool,
    check_coverage: bool,
    skip_catalog_check: bool,
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
    if skip_catalog_check:
        notes.append("application-id checks explicitly skipped (--skip-catalog-check)")
    else:
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


def _split(values: tuple[str, ...] | list[str]) -> list[str]:
    for value in values:
        if not any(item.strip() for item in str(value).split(",")):
            raise click.ClickException("explicit filter values must not be empty")
    return [
        item.strip()
        for value in values
        for item in str(value).split(",")
        if item.strip()
    ]


@offer_codes_command.command("find")
@click.option("--offer", "--offer-source", "offer_source", default=str(OFFER_SOURCE),
              show_default=True, metavar="SOURCE",
              help="Offer-codes directory or JSON file.")
@click.option("--app", "app_list", multiple=True, metavar="APP",
              help="Application id or alias, repeated or comma-separated.")
@click.option("--code", "code_list", multiple=True, metavar="CODE",
              help="Code to look up, repeated or comma-separated.")
@click.option("--platform", "platform_list", default=None, metavar="PLATFORMS",
              help="Comma-separated platform keys, or all.")
@click.option("--json", "as_json", is_flag=True, help="Print the result as JSON.")
def find_command(
    offer_source: str,
    app_list: tuple[str, ...],
    code_list: tuple[str, ...],
    platform_list: str | None,
    as_json: bool,
) -> None:
    """Find codes by application, code value or platform.

    With no filter every owned code is listed. A code shared by several
    applications produces one row per entry, which makes duplicate ownership
    visible.
    """
    documents = _load_local(offer_source, validate=True)
    platforms = (
        []
        if (platform_list or "").strip().lower() == "all"
        else _split([platform_list] if platform_list is not None else [])
    )
    rows = offer_codes.find_codes(
        documents,
        apps=_split(app_list),
        codes=_split(code_list),
        platforms=platforms,
    )

    if as_json:
        click.echo(json.dumps(
            {"codes": rows, "count": len(rows)}, indent=2, ensure_ascii=False
        ))
        return

    for row in rows:
        label = f" ({row['label']})" if row["label"] else ""
        kind = "" if row["type"] == "app" else f" [{row['type']}]"
        click.echo(f"{row['app']} {row['platform']} {row['code']}{label}{kind}")
    click.echo(f"{len(rows)} code(s)")


@offer_codes_command.command("add")
@click.option("--offer", "--offer-source", "offer_source", default=str(OFFER_SOURCE),
              show_default=True, metavar="SOURCE",
              help="Local offer-codes directory or JSON file.")
@click.option("--app", required=True, metavar="APP",
              help="Application id or applications alias that should own the code(s).")
@click.option("--platform", required=True, metavar="PLATFORM",
              help="Platform key: aliyun, huaweiyun, alibabacloud, huaweicloud, "
                   "azure or aws.")
@click.option("--code", "code_list", multiple=True, metavar="CODE",
              help="Code to add, repeated or comma-separated.")
@click.option("--label", "label_list", multiple=True, metavar="[CODE=]LABEL",
              help="Variant label; use CODE=LABEL when adding several codes.")
@click.option("--product-catalog", "--catalog", "catalog_source",
              default=CATALOG_URL, show_default=True, metavar="SOURCE",
              help="product_en.json URL or local JSON used to check the application.")
@click.option("--allow-shared", is_flag=True,
              help="Allow a code that another application already owns on the same "
                   "platform, for a shared bundle listing.")
@click.option("--skip-catalog-check", is_flag=True,
              help="Explicitly skip application catalog validation (offline maintenance).")
@click.option("--dry-run", is_flag=True, help="Print the diff without writing files.")
@click.option("--json", "as_json", is_flag=True,
              help="Print JSON; still writes unless --dry-run is also given.")
@_locked_edit
def add_command(
    offer_source: str,
    app: str,
    platform: str,
    code_list: tuple[str, ...],
    label_list: tuple[str, ...],
    catalog_source: str,
    allow_shared: bool,
    skip_catalog_check: bool,
    dry_run: bool,
    as_json: bool,
) -> None:
    """Add codes to an application entry, creating the entry when missing.

    A code already owned by the same application on the platform is rejected,
    and so is a code owned by another application: use offer-codes replace to
    move an existing code instead of duplicating it. ``--allow-shared`` permits
    intentional shared ownership, such as a bundle listing.
    """
    if "," in platform:
        raise click.ClickException("--platform must be a single platform key")
    documents = _load_local(offer_source)
    original = _snapshot(documents)
    catalog_ids, notes = _load_catalog(catalog_source, skip_catalog_check)
    try:
        resolved_app = offer_codes.resolve_entry_id(documents, app)
    except ValueError as error:
        raise click.ClickException(str(error)) from None
    _ensure_in_catalog(app, resolved_app, catalog_ids, catalog_source)

    codes = _split(code_list)
    if not codes:
        raise click.ClickException("at least one --code is required")

    baseline = offer_codes.verify(documents, catalog_ids=None, check_coverage=False)

    try:
        plan = offer_codes.add_codes(
            documents,
            app=app,
            platform=platform,
            codes=codes,
            labels=list(label_list),
            allow_shared=allow_shared,
        )
    except ValueError as error:
        raise click.ClickException(str(error)) from None

    result = offer_codes.verify(documents, catalog_ids=None, check_coverage=False)
    introduced = _new_errors(baseline, result)
    if introduced:
        details = "; ".join(
            f"{issue['app'] or 'dataset'} {issue['field']}: {issue['message']}"
            for issue in introduced
        )
        raise click.ClickException(
            f"add would introduce {len(introduced)} error(s); nothing written: {details}"
        )
    new_warnings = _new_issues(baseline, result, "warning")

    changes = (
        _changes(documents, original, plan["sources"])
        if (dry_run or as_json)
        else ""
    )
    written = not dry_run
    if written:
        offer_codes.write_documents(documents, plan["sources"], expected=original)

    if as_json:
        click.echo(json.dumps({
            "result": plan,
            "diff": changes or None,
            "written": written,
            "verification": {
                "errors": result["errors"],
                "warnings": result["warnings"],
                "newWarnings": new_warnings,
            },
            "notes": notes,
        }, indent=2, ensure_ascii=False))
        return

    for item in plan["added"]:
        label = f" ({item['label']})" if item["label"] else ""
        click.echo(
            f"Added {plan['platform']} {item['code']}{label}: {_app_label(plan)}"
        )
    if plan["created"]:
        click.echo(f"Created offer entry for {plan['app']}")
    for note in notes:
        click.echo(f"note: {note}")

    if dry_run:
        _print_new_warnings(new_warnings)
        click.echo(changes or "No document changes.")
        click.echo("Dry run: no files written.")
        return

    for source in plan["sources"]:
        click.echo(f"Wrote {source}")
    click.echo(
        f"Verified after add: {result['errors']} error(s), "
        f"{result['warnings']} warning(s)"
    )
    _print_new_warnings(new_warnings)
    if plan["platform"] == "aws":
        click.echo("Next steps:")
        click.echo(
            f"  mk offer preflight --app {plan['app']} --cloud aws "
            f"--offer-codes {','.join(item['code'] for item in plan['added'])}"
        )
        click.echo("  publish the listing content under the new code(s)")
    click.echo("  mk offer-codes verify")


@offer_codes_command.command("remove")
@click.option("--offer", "--offer-source", "offer_source", default=str(OFFER_SOURCE),
              show_default=True, metavar="SOURCE",
              help="Local offer-codes directory or JSON file.")
@click.option("--app", required=True, metavar="APP",
              help="Application id or applications alias whose code(s) should be removed.")
@click.option("--platform", required=True, metavar="PLATFORM",
              help="Platform key, for example aws.")
@click.option("--code", "code_list", multiple=True, metavar="CODE",
              help="Code to remove, repeated or comma-separated.")
@click.option("--delete-empty", is_flag=True,
              help="Delete the entry when no codes remain.")
@click.option("--dry-run", is_flag=True, help="Print the diff without writing files.")
@click.option("--json", "as_json", is_flag=True,
              help="Print JSON; still writes unless --dry-run is also given.")
@_locked_edit
def remove_command(
    offer_source: str,
    app: str,
    platform: str,
    code_list: tuple[str, ...],
    delete_empty: bool,
    dry_run: bool,
    as_json: bool,
) -> None:
    """Remove codes and their labels from one application entry."""
    if "," in platform:
        raise click.ClickException("--platform must be a single platform key")
    documents = _load_local(offer_source)
    codes = _split(code_list)
    if not codes:
        raise click.ClickException("at least one --code is required")

    original = _snapshot(documents)
    baseline = offer_codes.verify(documents, catalog_ids=None, check_coverage=False)

    try:
        plan = offer_codes.remove_codes(
            documents,
            app=app,
            platform=platform,
            codes=codes,
            delete_empty=delete_empty,
        )
    except ValueError as error:
        raise click.ClickException(str(error)) from None

    result = offer_codes.verify(documents, catalog_ids=None, check_coverage=False)
    introduced = _new_errors(baseline, result)
    if introduced:
        details = "; ".join(
            f"{issue['app'] or 'dataset'} {issue['field']}: {issue['message']}"
            for issue in introduced
        )
        raise click.ClickException(
            f"remove would introduce {len(introduced)} error(s); nothing written: {details}"
        )
    new_warnings = _new_issues(baseline, result, "warning")

    changes = (
        _changes(documents, original, plan["sources"])
        if (dry_run or as_json)
        else ""
    )
    written = not dry_run
    if written:
        offer_codes.write_documents(documents, plan["sources"], expected=original)

    if as_json:
        click.echo(json.dumps({
            "result": plan,
            "diff": changes or None,
            "written": written,
            "verification": {
                "errors": result["errors"],
                "warnings": result["warnings"],
                "newWarnings": new_warnings,
            },
        }, indent=2, ensure_ascii=False))
        return

    for code in plan["removed"]:
        click.echo(f"Removed {plan['platform']} {code}: {_app_label(plan)}")
    if plan["deletedApp"]:
        click.echo(f"Deleted the empty offer entry for {plan['app']}")
    elif plan["remainingCodes"]:
        click.echo(
            f"{plan['app']} still owns {plan['remainingCodes']} code(s)."
        )
    else:
        click.echo(
            f"{plan['app']} no longer owns any code; keep the entry with "
            "--delete-empty or remove it separately."
        )

    if dry_run:
        _print_new_warnings(new_warnings)
        click.echo(changes or "No document changes.")
        click.echo("Dry run: no files written.")
        return

    for source in plan["sources"]:
        click.echo(f"Wrote {source}")
    click.echo(
        f"Verified after remove: {result['errors']} error(s), "
        f"{result['warnings']} warning(s)"
    )
    _print_new_warnings(new_warnings)
    click.echo("Next steps:")
    click.echo("  mk offer-codes verify")


@offer_codes_command.command("replace")
@click.option("--offer", "--offer-source", "offer_source", default=str(OFFER_SOURCE),
              show_default=True, metavar="SOURCE",
              help="Local offer-codes directory or JSON file.")
@click.option("--from", "old_app", required=True, metavar="APP",
              help="Application id or applications alias that currently owns the code(s).")
@click.option("--to", "new_app", required=True, metavar="APP",
              help="Application id or applications alias that should own the code(s) after the move.")
@click.option("--platform", "platform_list", multiple=True, metavar="PLATFORMS",
              help="Repeated or comma-separated platform keys; default: every platform that has codes.")
@click.option("--code", "code_list", multiple=True, metavar="CODE",
              help="Code to move, repeated or comma-separated. Default: every code "
                   "on the selected platforms.")
@click.option("--product-catalog", "--catalog", "catalog_source",
              default=CATALOG_URL, show_default=True, metavar="SOURCE",
              help="product_en.json URL or local JSON used to check the target application.")
@click.option("--delete-empty", is_flag=True,
              help="Delete the old entry when no codes remain.")
@click.option("--skip-catalog-check", is_flag=True,
              help="Explicitly skip target catalog validation (offline maintenance).")
@click.option("--dry-run", is_flag=True, help="Print the diff without writing files.")
@click.option("--json", "as_json", is_flag=True,
              help="Print JSON; still writes unless --dry-run is also given.")
@_locked_edit
def replace_command(
    offer_source: str,
    old_app: str,
    new_app: str,
    platform_list: tuple[str, ...],
    code_list: tuple[str, ...],
    catalog_source: str,
    delete_empty: bool,
    skip_catalog_check: bool,
    dry_run: bool,
    as_json: bool,
) -> None:
    """Move offer codes from one application entry to another.

    Use this when a listing is delisted but its code must keep carrying a
    different application. The command moves each code and its variant label,
    checks the target application against product_en.json, verifies the edited
    dataset, and only then rewrites the local file. It does not change any
    marketplace listing content; publish the new application separately.
    """
    if old_app.strip() == new_app.strip():
        raise click.ClickException("--from and --to must be different applications")

    documents = _load_local(offer_source)
    original = _snapshot(documents)
    catalog_ids, notes = _load_catalog(catalog_source, skip_catalog_check)
    try:
        resolved_app = offer_codes.resolve_entry_id(documents, new_app)
    except ValueError as error:
        raise click.ClickException(str(error)) from None
    _ensure_in_catalog(new_app, resolved_app, catalog_ids, catalog_source)

    platforms = _split(platform_list) if platform_list else None
    codes = _split(code_list) if code_list else None
    baseline = offer_codes.verify(documents, catalog_ids=None, check_coverage=False)

    try:
        plan = offer_codes.replace_code(
            documents,
            old_app=old_app,
            new_app=new_app,
            platforms=platforms,
            codes=codes,
            delete_empty=delete_empty,
        )
    except ValueError as error:
        raise click.ClickException(str(error)) from None

    result = offer_codes.verify(documents, catalog_ids=None, check_coverage=False)
    introduced = _new_errors(baseline, result)
    if introduced:
        details = "; ".join(
            f"{issue['app'] or 'dataset'} {issue['field']}: {issue['message']}"
            for issue in introduced
        )
        raise click.ClickException(
            f"replace would introduce {len(introduced)} error(s); nothing written: {details}"
        )
    new_warnings = _new_issues(baseline, result, "warning")

    changes = (
        _changes(documents, original, plan["sources"])
        if (dry_run or as_json)
        else ""
    )
    written = not dry_run
    if written:
        offer_codes.write_documents(documents, plan["sources"], expected=original)

    if as_json:
        click.echo(json.dumps({
            "result": plan,
            "diff": changes or None,
            "written": written,
            "verification": {
                "errors": result["errors"],
                "warnings": result["warnings"],
                "newWarnings": new_warnings,
            },
            "notes": notes,
        }, indent=2, ensure_ascii=False))
        return

    for move in plan["moves"]:
        label = f" ({move['label']})" if move["label"] else ""
        click.echo(
            f"Moved {move['platform']} {move['code']}{label}: "
            f"{plan['from']} -> {plan['to']}"
        )
    if plan["created"]:
        click.echo(f"Created offer entry for {plan['to']}")
    if plan["deletedOld"]:
        click.echo(f"Deleted the empty offer entry for {plan['from']}")
    elif plan["oldRemainingCodes"]:
        click.echo(
            f"{plan['from']} still owns {plan['oldRemainingCodes']} code(s)."
        )
    else:
        click.echo(
            f"{plan['from']} no longer owns any code; keep the entry with --delete-empty "
            "or remove it separately."
        )
    for note in notes:
        click.echo(f"note: {note}")
    if plan["requestedFrom"] != plan["from"]:
        click.echo(
            f"note: {plan['requestedFrom']!r} is an alias of entry {plan['from']!r}"
        )
    if plan["requestedTo"] != plan["to"]:
        click.echo(
            f"note: {plan['requestedTo']!r} is an alias of entry {plan['to']!r}"
        )

    if dry_run:
        _print_new_warnings(new_warnings)
        click.echo(changes or "No document changes.")
        click.echo("Dry run: no files written.")
        return

    for source in plan["sources"]:
        click.echo(f"Wrote {source}")
    click.echo(
        f"Verified after replace: {result['errors']} error(s), "
        f"{result['warnings']} warning(s)"
    )
    _print_new_warnings(new_warnings)
    click.echo("Next steps:")
    aws_codes = [
        move["code"] for move in plan["moves"] if move["platform"] == "aws"
    ]
    if aws_codes:
        click.echo(
            f"  mk offer preflight --app {plan['to']} --cloud aws "
            f"--offer-codes {','.join(aws_codes)}"
        )
    click.echo("  publish the new application's listing content under the moved code(s)")
    click.echo("  mk offer-codes verify")


def _issue_key(issue: dict[str, str]) -> tuple[str, str, str, str, str]:
    return (
        issue.get("severity", ""),
        issue.get("source", ""),
        issue.get("app", ""),
        issue.get("field", ""),
        issue.get("message", ""),
    )


def _document_diff(source: str, before: str, document) -> str:
    after = offer_codes.serialize_document(document)
    if before == after:
        return ""
    return "".join(
        difflib.unified_diff(
            before.splitlines(True),
            after.splitlines(True),
            fromfile=f"{source} (current)",
            tofile=f"{source} (updated)",
        )
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
@click.option("--app", "app_list", multiple=True,
              help="Limit checks to application ids or aliases, repeated or comma-separated.")
@click.option("--code", "code_list", multiple=True,
              help="Limit checks to codes, repeated or comma-separated.")
@click.option("--strict", is_flag=True,
              help="Fail when any code is reported as not found.")
@click.option("--json", "as_json", is_flag=True, help="Print the full result as JSON.")
def listing_check_command(
    offer_source: str,
    platform_list: str,
    limit: int,
    app_list: tuple[str, ...],
    code_list: tuple[str, ...],
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
            documents, platforms=platforms, limit=limit,
            apps=_split(app_list), codes=_split(code_list),
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
    if summary["errors"] > 0 or (strict and summary["not_found"] > 0):
        raise click.ClickException(
            f"{summary['errors']} error(s), {summary['not_found']} not-found code(s)."
        )
