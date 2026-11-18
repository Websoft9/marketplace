from __future__ import annotations

import copy
import json
import time
from pathlib import Path

import click

from .. import catalog_data, catalog_file, store
from .. import offer as service
from .. import offer_assets
from .. import offer_report
from .. import offer_resolve
from ..marketplace import CATALOG_URL, CONFIGURED_PLATFORMS, OFFER_SOURCE, VERSIONS_URL, catalog_default_path


@click.group("offer")
def offer_command() -> None:
    """Generate, compare and publish AWS AMI listing metadata."""


def single_app(_ctx, _param, value):
    value = (value or "").strip()
    if not value or any(char.isspace() or char in ",，" for char in value):
        raise click.BadParameter("exactly one application id is required; lists are not supported")
    return value


def inputs(function):
    for option in (
        click.option("--app", required=True, callback=single_app,
                     help="One application id from data/.work/catalog.json; lists are not supported."),
        click.option("--catalog", "catalog_source", default=None, metavar="SOURCE",
                     help="Temporary Contentful catalog JSON. Default: data/.work/catalog.json."),
        click.option("--versions", default=VERSIONS_URL, metavar="SOURCE",
                     help="Application version source URL or local JSON."),
        click.option("--vendor", type=click.Path(exists=True, dir_okay=False, path_type=Path),
                     default=service.DATA / "vendor.json"),
        click.option("--template", type=click.Path(exists=True, dir_okay=False, path_type=Path),
                     default=service.DATA / "templates/aws-ami.json"),
    ):
        function = option(function)
    return function


def bundle(app, catalog_source, versions, vendor, template):
    try:
        item, version, version_options = catalog_data.load_application(
            key=app,
            catalog_source=catalog_source or str(catalog_default_path()),
            versions_source=versions,
        )
        return service.render(
            item, version, vendor, template, version_options=version_options
        )
    except Exception as error:
        raise click.ClickException(f"invalid offer data: {error}") from error


def output(value):
    click.echo(json.dumps(value, indent=2, ensure_ascii=False))


def labels_for(app, cloud):
    try:
        return service.variant_labels(app, cloud)
    except (OSError, ValueError) as error:
        raise click.ClickException(str(error)) from None


def _status_to_stderr(value):
    """Keep long-running apply status on stderr so stdout stays one JSON document."""
    click.echo(json.dumps(value, indent=2, ensure_ascii=False), err=True)


def _markdown_value(value):
    if isinstance(value, list):
        return "\n".join(
            f"- {item['Text']}: {item['Url']}" if isinstance(item, dict) else f"- {item}"
            for item in value
        )
    return str(value)


def preview_markdown(preview):
    target = preview.get("target") or {}
    lines = [
        f"# {preview.get('templateId', 'preview')}",
        "",
        f"> Target: {target.get('platform', '-')} / {target.get('service', '-')} / {target.get('entityType', '-')}",
        "",
        "## Content",
        "",
    ]
    for field, value in (preview.get("content") or {}).items():
        lines.extend([f"### {field}", "", _markdown_value(value), ""])
    resources = preview.get("AdditionalResources") or []
    if resources:
        lines.extend(["## AdditionalResources", "", _markdown_value(resources), ""])
    sections = preview.get("longDescriptionSections") or []
    if sections:
        lines.extend(["## Long description", ""])
        for section in sections:
            if section.get("heading"):
                lines.extend([f"### {section['heading']}", ""])
            lines.extend([section.get("text", ""), ""])
    reference_sections = preview.get("previewOnlySections") or []
    if reference_sections:
        lines.extend(["## Reference sections (not submitted)", ""])
        for section in reference_sections:
            if section.get("heading"):
                lines.extend([f"### {section['heading']}", ""])
            lines.extend([section.get("text", ""), ""])
    return "\n".join(lines).rstrip() + "\n"


def write_text_output(text, path):
    if path is None or str(path) == "-":
        click.echo(text, nl=False)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    click.echo(f"Wrote {path}", err=True)


def write_output(value, path):
    write_text_output(json.dumps(value, indent=2, ensure_ascii=False) + "\n", path)


def _selected_targets(value, all_codes, offer_codes):
    targets = value.get("targets") or []
    if not targets:
        raise click.ClickException(
            "Catalog EntityId is missing; run mk offer targets --app <id> --cloud <cloud> first"
        )
    requested = [str(code).strip() for code in (offer_codes or []) if str(code).strip()]
    if requested and all_codes:
        raise click.ClickException("use either --all-codes or --offer-code, not both")
    if requested:
        known = {target.get("code") for target in targets}
        unknown = [code for code in requested if code not in known]
        if unknown:
            raise click.ClickException(
                f"offer code(s) {', '.join(unknown)} are not recorded for this application"
            )
        wanted = set(requested)
        return [target for target in targets if target.get("code") in wanted]
    if all_codes:
        return targets
    if len(targets) > 1:
        raise click.ClickException(
            f"application has {len(targets)} Catalog targets; use --all-codes or --offer-code"
        )
    return targets[:1]


@offer_command.command("preflight")
@click.option("--app", required=True, callback=single_app, help="One application id from product_en.json, for example odoo.")
@click.option("--cloud", required=True, type=click.Choice(CONFIGURED_PLATFORMS),
              help="Target cloud platform from config/marketplace.json.")
@click.option("--offer-codes", "offer_codes", default=None, metavar="CODES",
              help="Comma-separated offer codes; default: every code recorded for the application.")
@click.option("--allow-unlisted-codes", is_flag=True,
              help="Accept explicit codes that the offer-codes matrix records under another application, "
                   "for example to reassign an existing offer.")
@click.option("--product-catalog", "--catalog-source", "catalog_source", default=CATALOG_URL,
              show_default=True, metavar="SOURCE",
              help="product_en.json URL or local path; the source of valid application ids.")
@click.option("--offer", "--offer-source", "offer_source", default=OFFER_SOURCE, show_default=True, metavar="SOURCE",
              help="Offer-codes directory or JSON file used to validate codes.")
@click.option("--timeout", type=float, default=30, show_default=True)
@click.option("--skip-code-check", is_flag=True,
              help="Validate only the application id; do not require offer codes from the local matrix.")
@click.option("--output", "output_path", type=click.Path(dir_okay=False, path_type=Path), metavar="PATH",
              help="Write the resolved target list here instead of stdout.")
def preflight_command(app, cloud, offer_codes, allow_unlisted_codes, catalog_source, offer_source, timeout, skip_code_check, output_path):
    """Validate an application id and its offer codes before any cloud call."""
    requested = [code.strip() for code in (offer_codes or "").split(",") if code.strip()]
    try:
        value = service.preflight(
            app, cloud, requested, catalog_source, offer_source, timeout,
            allow_unlisted=allow_unlisted_codes,
            skip_code_check=skip_code_check,
        )
    except (OSError, ValueError) as error:
        raise click.ClickException(str(error)) from error
    write_output(value, output_path)


@offer_command.command("targets")
@click.option("--app", required=True, callback=single_app, help="Application id from the pulled catalog.")
@click.option("--cloud", required=True, type=click.Choice(CONFIGURED_PLATFORMS),
              help="Target cloud platform from config/marketplace.json.")
@click.option("--catalog", "catalog_source", default=None, metavar="SOURCE",
              help="Temporary Contentful catalog JSON. Default: data/.work/catalog.json.")
@click.option("--offer-codes", "offer_codes", default=None, metavar="CODES",
              help="Comma-separated offer codes; default: every code recorded for the application.")
@click.option("--allow-unlisted-codes", is_flag=True,
              help="Accept explicit codes that the catalog does not record for this application, "
                   "for example to reassign an existing offer. Each code must still resolve to one entity.")
@click.option("--profile", default=None)
@click.option("--max-results", type=click.IntRange(1, 100), default=50, show_default=True)
@click.option("--entity-map", type=click.Path(dir_okay=False, path_type=Path), metavar="PATH",
              help="Optional code-to-EntityId cache; known codes skip the catalog scan.")
@click.option("--output", "output_path", type=click.Path(dir_okay=False, path_type=Path), metavar="PATH",
              help="Write the resolved targets here instead of stdout.")
def targets_command(app, cloud, catalog_source, offer_codes, allow_unlisted_codes, profile, max_results, entity_map, output_path):
    """Resolve offer codes to Catalog EntityIds and record them in the catalog."""
    catalog_path = Path(catalog_source or catalog_default_path())
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise click.ClickException(f"failed to read catalog {catalog_path}: {error}") from None
    if not isinstance(catalog, dict):
        raise click.ClickException(
            f"catalog {catalog_path} must be a catalog dataset with an items array"
        )
    items = catalog.get("items")
    if not isinstance(items, list):
        raise click.ClickException(f"catalog {catalog_path} is not a catalog dataset")
    item = next(
        (entry for entry in items if isinstance(entry, dict) and entry.get("id") == app), None
    )
    if item is None:
        raise click.ClickException(f"application {app!r} is absent from {catalog_path}")
    recorded = [
        str(code).strip()
        for code in ((item.get("offerCodes") or {}).get(cloud) or [])
        if str(code).strip()
    ]
    requested = list(dict.fromkeys(
        code.strip() for code in (offer_codes or "").split(",") if code.strip()
    ))
    unlisted: list[str] = []
    if requested:
        unlisted = [code for code in requested if code not in recorded]
        if unlisted and not allow_unlisted_codes:
            raise click.ClickException(
                f"offer code(s) {', '.join(unlisted)} do not belong to {app!r} on {cloud}"
            )
        codes = requested
    else:
        codes = recorded
    if not codes:
        raise click.ClickException(f"{app!r} has no {cloud} offer code in the catalog")
    known: dict[str, str] = {}
    if entity_map is not None and entity_map.is_file():
        try:
            known = json.loads(entity_map.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            known = {}
        if not isinstance(known, dict):
            known = {}
    try:
        matches = _resolve_aws_entities(
            codes, profile, max_results=max_results, known=known
        )
    except ValueError as error:
        raise click.ClickException(str(error)) from None
    not_found = [code for code in codes if not matches.get(code)]
    ambiguous = {
        code: [match["entityId"] for match in matches.get(code) or []]
        for code in codes
        if len(matches.get(code) or []) > 1
    }
    if not_found or ambiguous:
        details = []
        if not_found:
            details.append(f"no Catalog entity found for: {', '.join(not_found)}")
        if ambiguous:
            details.append(
                "ambiguous offer codes: "
                + ", ".join(f"{code} -> {', '.join(ids)}" for code, ids in ambiguous.items())
            )
        raise click.ClickException("; ".join(details))
    targets: list[dict] = []
    duplicates: dict[str, list[str]] = {}
    by_entity: dict[str, dict] = {}
    for code in codes:
        entity_id = matches[code][0]["entityId"]
        target = by_entity.get(entity_id)
        if target is None:
            target = {"code": code, "entityId": entity_id}
            by_entity[entity_id] = target
            targets.append(target)
        else:
            duplicates.setdefault(entity_id, [target["code"]]).append(code)
    item["targets"] = targets
    item["entityId"] = targets[0]["entityId"]
    catalog_path.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if entity_map is not None:
        entity_map.parent.mkdir(parents=True, exist_ok=True)
        merged_map = {**known, **{target["code"]: target["entityId"] for target in targets}}
        for entity_id, aliases in duplicates.items():
            for alias in aliases:
                merged_map[alias] = entity_id
        entity_map.write_text(
            json.dumps(merged_map, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    document = {"schemaVersion": 1, "app": app, "cloud": cloud, "targets": targets}
    if duplicates:
        document["duplicates"] = duplicates
    if unlisted:
        document["unlistedCodes"] = unlisted
    write_output(document, output_path)


@offer_command.command("resolve")
@inputs
@click.option("--cloud", required=True, type=click.Choice(CONFIGURED_PLATFORMS),
              help="Target cloud platform from config/marketplace.json.")
@click.option("--code", default=None, metavar="CODE",
              help="Target one offer code/SKU directly. A code that belongs to another "
                   "application is treated as a deliberate override.")
@click.option("--offer", default=None, metavar="OFFER",
              help="Offer variant from the application's multiOffers, for example Ubuntu, "
                   "Multisite, 'Ubuntu; Multisite', or base for the no-label offer.")
@click.option("--profile", default=None)
@click.option("--inventory", "inventory_path", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Use a complete public mk store products --json snapshot; no inventory re-query.")
@click.option("--visibility", type=click.Choice(store.VISIBILITIES), default="public", show_default=True,
              help="Explicit target scope; even --code cannot opt into nonpublic products.")
@click.option("--product-catalog", default=CATALOG_URL, metavar="SOURCE",
              help="Full product_en.json catalog for detecting other applications and ambiguous titles.")
@click.option("--output", "output_path", type=click.Path(dir_okay=False, path_type=Path), metavar="PATH",
              help="Write the resolved targets here instead of stdout.")
def resolve_command(app, catalog_source, versions, vendor, template, cloud, code, offer, profile, inventory_path, visibility, product_catalog, output_path):
    """Resolve public AWS store facts to offer targets by application.

    Matches the application by trademark in the live listing title/summary,
    detects OS and label variants, and records one code per variant in the
    temporary catalog for build, plan and apply.
    """
    if cloud not in service.APPLY_CLOUDS:
        raise click.ClickException(
            f"cloud {cloud!r} has no live resolver; implemented: "
            + ", ".join(service.APPLY_CLOUDS)
        )
    if inventory_path is not None and profile is not None:
        raise click.BadParameter("--profile cannot be combined with --inventory; snapshots are not re-queried")
    if code is not None and (not code.strip() or any(c in code for c in ",，")):
        raise click.BadParameter("provide exactly one non-empty code", param_hint="--code")
    code = code.strip() if code is not None else None
    if offer and offer.casefold() == "none":
        offer = None
    catalog_path = Path(catalog_source or catalog_default_path())
    expected = catalog_file.read_bytes(catalog_path)
    value = bundle(app, catalog_source, versions, vendor, template)
    try:
        offer_resolve.validate_offer_choice(value, offer)
        inventory = store.get_inventory(
            cloud, inventory_path=inventory_path, profile=profile, visibility=visibility,
            progress=lambda message: click.echo(message, err=True),
        )
        products = store.inventory_products(inventory, cloud)
        identities = offer_resolve.load_identities(product_catalog, service.load_special_cases())
        warnings = offer_resolve.catalog_warnings(identities)
        for warning in warnings:
            click.echo(f"WARNING identity catalog [{warning['appId']}]: {warning['message']}", err=True)
        document = offer_resolve.resolve(
            value, products, code=code, offer=offer, identities=identities
        )
        document["inventory"] = store.inventory_provenance(inventory, inventory_path)
        document["matching"] = {
            "rulesVersion": offer_resolve.IDENTITY_RULES_VERSION, "complete": True,
            "degraded": bool(warnings), "warnings": warnings,
        }
    except (OSError, ValueError, KeyError) as error:
        raise click.ClickException(str(error)) from error

    try:
        catalog = json.loads(expected)
    except (OSError, ValueError) as error:
        raise click.ClickException(f"failed to read catalog {catalog_path}: {error}") from None
    item = next(
        (
            entry
            for entry in (catalog.get("items") or [])
            if isinstance(entry, dict) and entry.get("id") == app
        ),
        None,
    )
    if item is None:
        raise click.ClickException(f"application {app!r} is absent from {catalog_path}")
    item["targets"] = [
        {
            "code": target.get("code") or None,
            "entityId": target["entityId"],
            "offer": target.get("offer", ""),
        }
        for target in document["targets"]
    ]
    item["entityId"] = document["targets"][0]["entityId"]
    try:
        catalog_file.replace(
            catalog_path, json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
            expected=expected,
        )
    except (OSError, ValueError) as error:
        raise click.ClickException(str(error)) from None
    write_output(document, output_path)


@offer_command.command("versions")
@click.option("--source", default=VERSIONS_URL, show_default=True, metavar="SOURCE",
              help="Application version source URL or local JSON.")
@click.option("--output", "output_path", required=True,
              type=click.Path(dir_okay=False, path_type=Path), metavar="PATH",
              help="Write the version snapshot here.")
def versions_command(source, output_path):
    """Snapshot the application version source used by build, plan and apply."""
    try:
        value = service.versions_snapshot(source)
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    write_output(value, output_path)


@offer_command.command("doctor")
@click.option("--cloud", required=True, type=click.Choice(CONFIGURED_PLATFORMS),
              help="Target cloud platform from config/marketplace.json.")
@click.option("--plan-role-arn", default=None, metavar="ARN",
              help="Validate the read-only planning role; an empty value fails.")
@click.option("--apply-role-arn", default=None, metavar="ARN",
              help="Validate the write apply role; an empty value fails.")
def doctor_command(cloud, plan_role_arn, apply_role_arn):
    """Check cloud support and role configuration before plan or apply."""
    try:
        if cloud not in service.APPLY_CLOUDS:
            raise ValueError(
                f"cloud {cloud!r} has no offer plan/apply implementation; "
                f"implemented: {', '.join(service.APPLY_CLOUDS)}"
            )
        checked = []
        if plan_role_arn is not None:
            service.require_role_arn(cloud, "plan", plan_role_arn)
            checked.append("plan")
        if apply_role_arn is not None:
            service.require_role_arn(cloud, "apply", apply_role_arn)
            checked.append("apply")
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    write_output(
        {
            "schemaVersion": 1,
            "cloud": cloud,
            "implemented": True,
            "checkedRoles": checked,
        },
        None,
    )


@offer_command.command("build")
@inputs
@click.option("--cloud", required=True, type=click.Choice(CONFIGURED_PLATFORMS),
              help="Target cloud platform from config/marketplace.json. Must match the template target.")
@click.option("--offer-codes", "offer_codes", default=None, metavar="CODES",
              help="Comma-separated offer codes to preview; default: every code recorded in the catalog for the cloud.")
@click.option("--allow-unlisted-codes", is_flag=True,
              help="Preview explicit codes that the catalog does not record for this application, "
                   "for example to reassign an existing offer.")
@click.option("--output", "output_path", type=click.Path(dir_okay=False, path_type=Path), metavar="PATH",
              help="Default: data/.work/<cloud>/<key>.json; use - for stdout.")
@click.option("--publish-ready", is_flag=True,
              help="Fail without writing when required version or Catalog identity is missing.")
def build_command(app, catalog_source, versions, vendor, template, cloud, offer_codes, allow_unlisted_codes, output_path, publish_ready):
    """Build a human-review preview matching the authoring template."""
    if allow_unlisted_codes and offer_codes is None:
        raise click.ClickException("--allow-unlisted-codes requires explicit --offer-codes")
    value = bundle(app, catalog_source, versions, vendor, template)
    preview = value["preview"]
    target_platform = (preview.get("target") or {}).get("platform")
    if target_platform != cloud:
        raise click.ClickException(
            f"template target platform {target_platform!r} does not match --cloud {cloud!r}"
        )
    truncation = preview.get("keywordTruncation")
    if truncation:
        click.echo(
            f"WARNING {app}: SearchKeywords truncated {truncation['original']} -> "
            f"{truncation['kept']} entries (combined {truncation['originalCharacters']} -> "
            f"{truncation['combinedCharacters']} characters)",
            err=True,
        )
    if publish_ready and value["publishBlockers"]:
        raise click.ClickException(
            "offer is valid as a draft but not ready to publish: "
            + "; ".join(value["publishBlockers"])
        )
    resolved = value.get("targets") or []
    if resolved:
        if allow_unlisted_codes:
            raise click.ClickException("--allow-unlisted-codes cannot be combined with resolved targets; run resolve again")
        requested = list(dict.fromkeys(
            code.strip() for code in (offer_codes or "").split(",") if code.strip()
        ))
        if offer_codes is not None and not requested:
            raise click.ClickException("--offer-codes must not be empty")
        known = {target.get("code") for target in resolved}
        unknown = set(requested) - known
        if unknown:
            raise click.ClickException("code(s) not in resolved targets: " + ", ".join(sorted(unknown)))
        selected = [t for t in resolved if not requested or t.get("code") in requested]
        codes = [target["code"] for target in selected if target.get("code")]
        if any("offer" in target for target in selected):
            labels = {t["code"]: t.get("offer", "") for t in selected if t.get("code")}
        else:
            labels = labels_for(app, cloud)
    else:
        labels = labels_for(app, cloud)
        recorded = [
            code
            for code in (value.get("offerCodes") or {}).get(cloud) or []
            if isinstance(code, str) and code.strip()
        ]
        requested = list(dict.fromkeys(
            code.strip() for code in (offer_codes or "").split(",") if code.strip()
        ))
        if requested:
            unlisted = [code for code in requested if code not in recorded]
            if unlisted and not allow_unlisted_codes:
                raise click.ClickException(
                    f"offer code(s) {', '.join(unlisted)} are not recorded for {app!r} on {cloud}; "
                    "use --allow-unlisted-codes to preview a forced reassignment"
                )
            codes = requested
        else:
            codes = recorded
        if offer_codes is not None and not requested:
            raise click.ClickException("--offer-codes must not be empty")
    warnings = []
    if requested or len(codes) > 1:
        targets = []
        for code in codes:
            label = labels.get(code, "")
            variant = service.with_variant(value, label, strict=False)
            warnings.extend(
                {"code": code, **warning}
                for warning in variant.get("titleWarnings") or []
            )
            targets.append(
                {"code": code, "label": label, "preview": variant["preview"]}
            )
        document = {
            "schemaVersion": 1,
            "key": value["key"],
            "cloud": cloud,
            "targets": targets,
        }
    else:
        code = codes[0] if codes else None
        variant = service.with_variant(value, labels.get(code, ""), strict=False)
        warnings = [
            {"code": code, **warning} for warning in variant.get("titleWarnings") or []
        ]
        document = variant["preview"]
        if code:
            document["offerCode"] = code
    if value["publishBlockers"]:
        document["publishBlockers"] = value["publishBlockers"]
        click.echo(
            f"WARNING {app}: draft is not publish-ready: "
            + "; ".join(value["publishBlockers"]),
            err=True,
        )
    if warnings:
        document["titleWarnings"] = warnings
        for warning in warnings:
            click.echo(
                f"WARNING {app} {warning.get('code') or '-'}: ProductTitle is "
                f"{warning['length']} characters, maximum {warning['max']}: "
                f"{warning['title']}",
                err=True,
            )
    target = output_path or (service.ROOT / "data" / ".work" / cloud / f"{value['key']}.json")
    if str(target) != "-" and target.suffix.lower() in {".md", ".markdown"}:
        if isinstance(document, dict) and document.get("targets") is not None:
            text = "\n\n".join(
                f"## {item['code']} ({item['label'] or 'no label'})\n\n"
                + preview_markdown(item["preview"])
                for item in document["targets"]
            )
        else:
            text = preview_markdown(document)
        write_text_output(text, target)
    else:
        write_output(document, target)


@offer_command.group("assets")
def assets_command():
    """Build AWS logo and promotional-media previews separately from copy."""


@assets_command.command("build")
@click.option("--app", required=True, callback=single_app,
              help="One application id from data/.work/catalog.json; lists are not supported.")
@click.option("--catalog", "catalog_source", default=None, metavar="SOURCE",
              help="Temporary Contentful catalog JSON. Default: data/.work/catalog.json.")
@click.option("--template", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              default=offer_assets.ASSETS_TEMPLATE,
              help="Asset authoring template. Default: data/listing/templates/aws-ami-assets.json.")
@click.option("--output", "output_path", type=click.Path(dir_okay=False, path_type=Path), metavar="PATH",
              help="Default: data/.work/aws/<key>-assets.json; use - for stdout.")
def assets_build_command(app, catalog_source, template, output_path):
    """Render LogoUrl and up to five PromotionalMedia images without AWS calls."""
    source = catalog_source or str(catalog_default_path())
    try:
        _, by_id = catalog_data.load_catalog(source)
        item = by_id.get(app)
        if item is None:
            raise ValueError(f"application {app!r} is not in the catalog")
        value = offer_assets.render_assets(item, template)
    except (OSError, ValueError) as error:
        raise click.ClickException(str(error)) from error
    for warning in value.get("warnings") or []:
        click.echo(f"WARNING {app}: {warning}", err=True)
    target = output_path or (service.ROOT / "data" / ".work" / "aws" / f"{app}-assets.json")
    write_output(value, target)


@offer_command.command("titles")
@click.option("--cloud", required=True, type=click.Choice(["aws"]),
              help="Target cloud platform (currently: aws).")
@click.option("--apps", "app_list", default=None, metavar="IDS",
              help="Comma-separated application ids. Default: every catalog item.")
@click.option("--catalog", "catalog_source", default=None, metavar="SOURCE",
              help="Temporary Contentful catalog JSON. Default: data/.work/catalog.json.")
@click.option("--versions", default=VERSIONS_URL, metavar="SOURCE",
              help="Application version source URL or local JSON.")
@click.option("--vendor", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              default=service.DATA / "vendor.json")
@click.option("--template", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              default=service.DATA / "templates/aws-ami.json")
@click.option("--max", "max_length", type=click.IntRange(1), default=72, show_default=True,
              help="Maximum ProductTitle characters.")
@click.option("--strict", is_flag=True,
              help="Exit non-zero when any title exceeds the maximum.")
@click.option("--json", "as_json", is_flag=True,
              help="Print the complete result as JSON.")
def titles_command(app_list, cloud, catalog_source, versions, vendor, template, max_length, strict, as_json):
    """Render every ProductTitle and report those over the AWS limit."""
    source = catalog_source or str(catalog_default_path())
    try:
        _, by_id = catalog_data.load_catalog(source)
        version_map = catalog_data.load_versions(versions)
        variants = service.offer_variants_by_app(OFFER_SOURCE)
    except (OSError, ValueError) as error:
        raise click.ClickException(str(error)) from None

    requested = [item.strip() for item in (app_list or "").split(",") if item.strip()]
    unknown = [app for app in requested if app not in by_id]
    if unknown:
        raise click.ClickException(
            f"application(s) not in the catalog: {', '.join(unknown)}"
        )
    apps = requested or list(by_id)

    titles = []
    errors = []
    for app in apps:
        item = by_id[app]
        options = version_map.get(app, [])
        version = options[0] if options else ""
        try:
            value = service.render(
                item, version, vendor, template, version_options=options
            )
        except (OSError, ValueError) as error:
            errors.append({"app": app, "error": str(error)})
            continue
        labels = (variants.get(app) or {}).get(cloud) or {}
        codes = [
            code
            for code in (item.get("offerCodes") or {}).get(cloud) or []
            if isinstance(code, str) and code.strip()
        ] or [None]
        for code in codes:
            label = labels.get(code, "") if code else ""
            try:
                variant = service.with_variant(value, label, strict=False)
            except (OSError, ValueError) as error:
                errors.append({"app": app, "code": code, "error": str(error)})
                continue
            title = variant["content"]["ProductTitle"]
            titles.append(
                {
                    "app": app,
                    "code": code,
                    "label": label,
                    "title": title,
                    "length": len(title),
                    "over": len(title) > max_length,
                }
            )

    over = [entry for entry in titles if entry["over"]]
    document = {
        "schemaVersion": 1,
        "cloud": cloud,
        "max": max_length,
        "titles": titles,
        "over": [
            {"app": entry["app"], "code": entry["code"], "length": entry["length"]}
            for entry in over
        ],
        "errors": errors,
    }
    if as_json:
        output(document)
    else:
        click.echo(f"{'APP':<20} {'CODE':<28} {'LEN':>4}  TITLE")
        for entry in titles:
            code = entry["code"] or "-"
            marker = "!" if entry["over"] else " "
            click.echo(
                f"{entry['app']:<20} {code:<28} {entry['length']:>4}{marker} {entry['title']}"
            )
        for error in errors:
            click.echo(f"SKIP {error['app']}: {error['error']}", err=True)
        click.echo(
            f"Checked {len(titles)} title(s): {len(over)} over {max_length} characters"
        )
        if over:
            click.echo(
                "Over limit: "
                + ", ".join(
                    f"{entry['app']} ({entry['code'] or '-'}) {entry['length']}"
                    for entry in over
                )
            )
        if errors:
            click.echo(f"{len(errors)} application(s) could not be rendered", err=True)
    if strict and over:
        raise click.ClickException(
            f"{len(over)} title(s) exceed {max_length} characters"
        )


def _entity_match(entity_id, code, detail, summary=None):
    """Return a match record when one code belongs to the described entity."""
    document = detail.get("DetailsDocument")
    if document is None:
        try:
            document = json.loads(detail.get("Details") or "{}")
        except (TypeError, ValueError):
            return None
    description = document.get("Description") or {}
    sku = str(description.get("Sku") or "").strip()
    product_code = str(description.get("ProductCode") or "").strip()
    if code not in {sku, product_code, entity_id}:
        return None
    summary = summary or {}
    return {
        "cloud": "aws",
        "entityId": entity_id,
        "title": summary.get("ProductTitle") or description.get("ProductTitle") or "",
        "visibility": summary.get("Visibility") or description.get("Visibility", ""),
        "sku": sku,
        "productCode": product_code,
        "code": code,
    }


def _resolve_aws_entities(codes, profile, max_results=50, known=None):
    """Resolve codes with a known-map fast path and early exit over the catalog."""
    client = service.aws_client(profile)
    wanted = list(dict.fromkeys(codes))
    matches = {code: [] for code in wanted}
    for code in wanted:
        entity_id = (known or {}).get(code)
        if not entity_id:
            continue
        try:
            detail = service.aws_call(client, "describe_entity", EntityId=entity_id)
        except ValueError:
            continue
        match = _entity_match(entity_id, code, detail)
        if match:
            matches[code] = [match]
    remaining = {code for code in wanted if not matches[code]}
    token = None
    while remaining:
        request = {"EntityType": "AmiProduct", "MaxResults": max_results}
        if token:
            request["NextToken"] = token
        response = service.aws_call(client, "list_entities", **request)
        for item in response.get("EntitySummaryList", []):
            entity_id = item.get("EntityId", "")
            if not entity_id:
                continue
            detail = service.aws_call(client, "describe_entity", EntityId=entity_id)
            for code in list(remaining):
                match = _entity_match(
                    entity_id, code, detail, item.get("AmiProductSummary") or {}
                )
                if match:
                    matches[code] = [match]
                    remaining.discard(code)
            if not remaining:
                break
        token = response.get("NextToken")
        if not token:
            break
    return matches


@offer_command.command("resolve-entity")
@click.option("--cloud", required=True, type=click.Choice(["aws"]),
              help="Cloud platform (currently: aws).")
@click.option("--code", "codes", required=True, multiple=True,
              help="Marketplace offer code, SKU, or product code. Repeat for multiple codes.")
@click.option("--profile", default=None)
def resolve_entity_command(cloud, codes, profile):
    """Resolve marketplace offer codes to seller Catalog EntityIds."""
    try:
        if cloud not in service.APPLY_CLOUDS:
            raise click.ClickException(
                f"unsupported cloud: {cloud}; implemented: {', '.join(service.APPLY_CLOUDS)}"
            )
        unique_codes = list(dict.fromkeys(codes))
        matches = _resolve_aws_entities(unique_codes, profile)
        results = []
        not_found = []
        ambiguous = {}
        for code in unique_codes:
            found = matches.get(code, [])
            if len(found) == 1:
                results.append(found[0])
            elif not found:
                not_found.append(code)
            else:
                ambiguous[code] = [item["entityId"] for item in found]
        if len(unique_codes) == 1:
            if not_found:
                raise click.ClickException(
                    f"no AWS AMI product found for offer code {not_found[0]!r}"
                )
            if ambiguous:
                code = next(iter(ambiguous))
                raise click.ClickException(
                    f"offer code {code!r} is ambiguous: {', '.join(ambiguous[code])}"
                )
            output(results[0])
            return
        output({"results": results, "notFound": not_found, "ambiguous": ambiguous})
        if not_found or ambiguous:
            raise click.exceptions.Exit(1)
    except ValueError as error:
        raise click.ClickException(str(error)) from error


@offer_command.command("plan")
@inputs
@click.option("--current", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Offline DescribeEntity fixture; single target only.")
@click.option("--profile", default=None)
@click.option("--all-codes", is_flag=True,
              help="Plan every offer code recorded for the application.")
@click.option("--offer-code", "offer_codes", multiple=True,
              help="Restrict planning to this offer code; repeatable.")
@click.option("--output", "output_path", type=click.Path(dir_okay=False, path_type=Path), metavar="PATH",
              help="Write the complete plan/request here instead of stdout.")
def plan_command(app, catalog_source, versions, vendor, template, current, profile, all_codes, offer_codes, output_path):
    """Produce a revision-bound UpdateInformation diff without submitting it."""
    value = bundle(app, catalog_source, versions, vendor, template)
    targets = _selected_targets(value, all_codes, offer_codes)
    if any("offer" in target for target in targets):
        labels = {
            target.get("code"): target.get("offer", "")
            for target in targets
            if target.get("code")
        }
    else:
        labels = labels_for(
            app, (value["preview"].get("target") or {}).get("platform") or "aws"
        )
    try:
        if len(targets) == 1 and not all_codes:
            entity = service.read_json(current) if current else service.aws_call(
                service.aws_client(profile), "describe_entity", EntityId=targets[0]["entityId"]
            )
            bundle_value = service.with_variant(
                {**value, "entityId": targets[0]["entityId"]},
                labels.get(targets[0].get("code"), ""),
            )
            write_output(service.plan(bundle_value, entity), output_path)
            return
        if current:
            raise ValueError("--current supports a single target only; select one offer code")
        client = service.aws_client(profile)
        entities = [
            {
                "code": target.get("code"),
                "entityId": target["entityId"],
                "entity": service.aws_call(client, "describe_entity", EntityId=target["entityId"]),
            }
            for target in targets
        ]
        write_output(service.multi_plan(value, entities, labels), output_path)
    except (ValueError, KeyError, OSError) as error:
        raise click.ClickException(str(error)) from error


def submit_target(client, target):
    """Submit one planned target and return its result entry."""
    entry = {
        "code": target.get("code"),
        "entityId": target["entityId"],
        "planHash": target["plan"]["planHash"],
    }
    request = target["plan"]["request"]
    if request is None:
        return {**entry, "status": "NO_CHANGES"}
    request = copy.deepcopy(request)
    request.pop("Catalog")
    request["ClientRequestToken"] = target["plan"]["planHash"]
    response = service.aws_call(client, "start_change_set", **request)
    return {**entry, "changeSetId": response["ChangeSetId"], "status": "SUBMITTED"}


@offer_command.command("apply")
@inputs
@click.option("--plan", "plan_path", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--profile", default=None)
@click.option("--wait-seconds", type=click.IntRange(min=0), default=0, help="Poll for up to this many seconds; timeout does not cancel the change.")
def apply_command(app, catalog_source, versions, vendor, template, plan_path, profile, wait_seconds):
    """Submit exactly the reviewed plan after rechecking source and live revision."""
    value = bundle(app, catalog_source, versions, vendor, template)
    if value["publishBlockers"]:
        raise click.ClickException("publication blocked: " + "; ".join(value["publishBlockers"]))
    resolved = value.get("targets") or []
    if any("offer" in target for target in resolved):
        labels = {
            target.get("code"): target.get("offer", "")
            for target in resolved
            if target.get("code")
        }
    else:
        labels = labels_for(
            app, (value["preview"].get("target") or {}).get("platform") or "aws"
        )
    try:
        reviewed = service.read_json(plan_path)
        client = service.aws_client(profile)
        if isinstance(reviewed.get("targets"), list):
            entities = [
                {
                    "code": target.get("code"),
                    "entityId": target["entityId"],
                    "entity": service.aws_call(client, "describe_entity", EntityId=target["entityId"]),
                }
                for target in reviewed["targets"]
            ]
            expected = service.multi_plan(value, entities, labels)
            if reviewed != expected:
                raise ValueError("plan is stale or modified; regenerate and review it")
            results = []
            failure = None
            for target in expected["targets"]:
                try:
                    results.append(submit_target(client, target))
                except (KeyError, ValueError) as error:
                    results.append({
                        "code": target.get("code"),
                        "entityId": target["entityId"],
                        "planHash": target["plan"]["planHash"],
                        "status": "FAILED",
                        "error": str(error),
                    })
                    failure = error
                    break
            submitted = [result for result in results if result["status"] == "SUBMITTED"]
            status = "PARTIAL" if failure else ("SUBMITTED" if submitted else "NO_CHANGES")
            document = {
                "schemaVersion": 1,
                "key": value["key"],
                "status": status,
                "targets": results,
                "skipped": [
                    target.get("code")
                    for target in expected["targets"][len(results):]
                ],
            }
            if failure:
                document["resumeHint"] = (
                    "Re-run the workflow to plan the remaining offer codes. "
                    "Targets that were already updated plan to NO_CHANGES."
                )
            output(document)
            if failure:
                raise click.exceptions.Exit(1)
            if wait_seconds:
                for result in submitted:
                    poll(
                        client, result["changeSetId"], wait_seconds,
                        service.with_variant(
                            {**value, "entityId": result["entityId"]},
                            labels.get(result.get("code"), ""),
                        ),
                        emit=_status_to_stderr,
                    )
            return
        single = (reviewed.get("baseRevision") or "").split("@")[0]
        if not single:
            raise ValueError("reviewed plan has no base revision")
        live = service.aws_call(client, "describe_entity", EntityId=single)
        single_code = next(
            (
                target.get("code")
                for target in value.get("targets") or []
                if target.get("entityId") == single
            ),
            None,
        )
        bundle_value = service.with_variant(
            {**value, "entityId": single}, labels.get(single_code, "")
        )
        expected = service.plan(bundle_value, live)
        if reviewed != expected:
            raise ValueError("plan is stale or modified; regenerate and review it")
        if expected["request"] is None:
            output({"status": "NO_CHANGES"})
            return
        request = copy.deepcopy(expected["request"])
        request.pop("Catalog")
        request["ClientRequestToken"] = expected["planHash"]
        response = service.aws_call(client, "start_change_set", **request)
        change_id = response["ChangeSetId"]
        output({"changeSetId": change_id, "status": "SUBMITTED", "planHash": expected["planHash"]})
        if wait_seconds:
            poll(client, change_id, wait_seconds, bundle_value, emit=_status_to_stderr)
    except (ValueError, KeyError, OSError) as error:
        raise click.ClickException(str(error)) from error


def poll(client, change_id, wait_seconds, value=None, emit=None):
    """Poll one ChangeSet; ``emit`` keeps apply's stdout a single JSON document."""
    emit = emit or output
    deadline = time.monotonic() + wait_seconds
    last_status = None
    while True:
        status = service.change_status(service.aws_call(client, "describe_change_set", ChangeSetId=change_id))
        if status["status"] != last_status:
            click.echo(f"ChangeSet {change_id}: {status['status']}", err=True)
            last_status = status["status"]
        if status["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            emit(status)
            if status["status"] != "SUCCEEDED":
                raise ValueError(f"ChangeSet ended with {status['status']}")
            if value:
                live = service.aws_call(client, "describe_entity", EntityId=value["entityId"])
                if service.plan(value, live)["request"] is not None:
                    raise ValueError("ChangeSet succeeded but read-back differs; inspect the product before retrying")
                emit({"readBackVerified": True, "entityId": value["entityId"]})
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            emit({**status, "pollingTimedOut": True})
            return
        time.sleep(min(5, remaining))


@offer_command.command("status")
@click.argument("change_set_id")
@click.option("--profile", default=None)
@click.option("--wait-seconds", type=click.IntRange(min=0), default=0)
def status_command(change_set_id, profile, wait_seconds):
    """Report ChangeSet status/error codes without dumping its private payload."""
    try:
        poll(service.aws_client(profile), change_set_id, wait_seconds)
    except ValueError as error:
        raise click.ClickException(str(error)) from error


@offer_command.command("report")
@click.argument("kind", type=click.Choice(["preflight", "resolve", "plan", "apply"]))
@click.option("--input", "input_path", required=True,
              type=click.Path(exists=True, dir_okay=False, path_type=Path), metavar="PATH",
              help="Preflight, plan or apply JSON document.")
@click.option("--cloud", default=None,
              help="Cloud label; enables the entity ARN for aws plans.")
@click.option("--app-name", "app_name", default=None,
              help="Display name used in the plan title.")
@click.option("--account-id", "account_id", default=None,
              help="AWS account id used to render entity ARNs.")
@click.option("--output", "output_path", type=click.Path(dir_okay=False, path_type=Path), metavar="PATH",
              help="Write Markdown here instead of stdout.")
def report_command(kind, input_path, cloud, app_name, account_id, output_path):
    """Render a preflight, plan or apply document as Markdown."""
    try:
        value = service.read_json(input_path)
    except (OSError, ValueError) as error:
        raise click.ClickException(f"failed to read {input_path}: {error}") from None
    if not isinstance(value, dict):
        raise click.ClickException(f"{input_path} must contain a JSON object")
    if kind == "preflight":
        text = offer_report.preflight(value)
    elif kind == "resolve":
        text = offer_report.resolve_targets(value)
    elif kind == "plan":
        text = offer_report.plan(
            value, cloud=cloud, app_name=app_name, account_id=account_id
        )
    else:
        text = offer_report.apply(value, cloud=cloud)
    write_text_output(text, output_path)
