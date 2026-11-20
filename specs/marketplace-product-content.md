# Marketplace Product Content and AWS Metadata Updates

## Scope and ownership

Supports product expressiveness and transactionability through reusable master
data, personalized listing copy and AWS Catalog metadata updates. This initial
implementation supports **existing `AmiProduct@1.0` listings** and
`UpdateInformation`; creation, delivery-option/version updates, pricing, refunds
and visibility changes require their own platform workflows.

`data/listing/aws.md` remains the supplied writing example. Its Odoo title and
JupyterHub short description conflict; do not use that historical example as
source data. Application and vendor content is maintained in its authoritative
source and rendered deterministically by the template. Do not repeat a
per-application fact audit for unchanged source data and template output.

## Data model

| File | Responsibility |
| --- | --- |
| `data/.work/catalog.json` | Temporary Contentful application facts; git-ignored and not authoritative |
| `data/listing/vendor.json` | Shared vendor identity, highlights, console capabilities, support and trademark text |
| `data/listing/templates/aws-ami.json` | Fixed copy structure with named placeholders, no credentials or per-product IDs |
| `data/listing/templates/aws-ami-assets.json` | Separate logo/promotional-media template; no copy fields |
| `cli/aws_marketplace.py` | AWS Marketplace adapter: field names/limits, read normalization, update request shape |
| `.github/workflows/offer-update.yml` | Manual AWS listing publication: preflight, preview, revision-bound plan, environment approval and apply |

## Layered architecture

Keep three layers separate:

1. **Source metadata** — application facts, optional AWS binding/category/media
   fields come temporarily from `data/.work/catalog.json`; vendor statements
   and templates stay in the repository. None of these files need to mirror a
   cloud API hierarchy.
2. **Authoring template** — `aws-ami.json` controls copy structure, placeholders,
   SearchKeywords sources and default Learning resources. It is not an AWS payload.
3. **AWS adapter** — `cli/aws_marketplace.py` translates between the authoring
   model and AWS. It owns field names/limits, read normalization and the update
   request shape.

`cli/aws_marketplace.py` declares the field contract in `FIELD_SPECS`: read
path, write field, type, limits, and read transformations. `CONTENT_FIELDS`,
`FIELD_LIMITS`, `current_content()` and `to_update_document()` derive from this
contract. Tests assert that template fields stay inside the contract and that
the read/write shapes round-trip.

AWS-facing names are aligned at the adapter boundary: `ProductTitle`,
`ShortDescription`, `LongDescription`, `Highlights`, `SearchKeywords`,
`SupportDescription`, `Categories`, `LogoUrl`, `VideoUrls`,
`PromotionalMedia`, `AdditionalResources`. The nested read shape is not copied
into authoring data.

The template declares its logical namespaces in `namespaces` and uses
namespace-qualified paths that map directly to source fields:

- `{catalog.*}` — temporary Contentful application facts, such as
  `{catalog.trademark}`, `{catalog.summary}`, `{catalog.usecases}`,
  `{catalog.keywords}`, or `{catalog.overview}`
- `{branding.*}` — presentation-only title branding; `{branding.titleTrademark}`
  defaults to `catalog.trademark` and is overridden per application in
  `data/listing/app-special-cases.json`
- `{versions.*}` — selected application version, such as `{versions.version}`, the space-prefixed `{versions.choice}` used by `ShortDescription`, and the multi-version suffix `{versions.selection}`
- `{vendor.*}` — vendor file, including indexed values such as
  `{vendor.highlights[0]}`

Actual source locations remain in `config/marketplace.json` (offer) and
`config/app.json` (catalog and versions), or in command options;
the template only names logical namespaces. A whole field can use an array
placeholder, for example `"SearchKeywords": "{catalog.keywords}"`. Arrays
interpolated into text use an explicit filter such as
`{catalog.usecases | join: ', '}`. The optional second `join` argument adds a
suffix only when the list is non-empty, so
`{catalog.relatedApps | join: ', ', ', '}` inserts related apps before fixed text
without leaving a leading comma. Source data values are literal; placeholders
belong only in templates. For example, the template combines
`{catalog.trademark}` with `{vendor.trademarkStatement}` instead of embedding a
placeholder in `vendor.json`. Platform-specific wording such as AWS billing or
infrastructure text belongs in the platform template, not the shared vendor
file. Undeclared namespaces and missing fields fail
rendering instead of silently producing empty text. There is no intermediate
runtime product or product schema.

The read shape differs from the update shape:

- Read: `Description.*`, `PromotionalResources.LogoUrl/Videos/PromotionalMedia/AdditionalResources`,
  `SupportInformation.Description`.
- Update: flat `UpdateInformation` fields, including `VideoUrls`,
  `PromotionalMedia` and `SupportDescription`.
- Read-side `AdditionalResources` items include `Type`; update-side items use
  `Text`/`Url`. The adapter removes `Type` for comparison and does not add it to
  the update payload.
- `LongDescription` is assembled from template sections; it is not a source field.
When the template sets `boldLongDescriptionHeadings`, non-empty section headings
are emitted as AWS-supported Markdown bold (`**Heading**`) in the submitted
description while the structured preview keeps plain headings. Each heading is
followed by a blank line so Markdown renders it as its own paragraph rather than
joining it to the body text.
`previewOnlySections` (currently Usage and Refunds) are rendered into the local
preview but are never added to `content`, `plan`, or the `UpdateInformation`
ChangeSet. Their `id` values leave room for separate Usage/Refund workflows later.

Only the AWS AMI binding exists today; future platforms must add explicit
templates/adapters rather than interpreting AWS fields for other platforms.
Existing cloud task JSON schemas are unaffected.

Application versions are supplied by `apps-verson.json`. An entry may provide a
single `version` string, or a list via `version`/`versions`; the first value is
the selected version used by `{versions.version}`.

`data/listing/app-special-cases.json` marks applications with `multiVersion: true`.
For those ids, when the versions source provides two or more values, the "What
is included?" section appends `<v1>/<v2>/... for selection` after the trademark.
The literal `latest` value is ignored, and single-value apps keep the existing
wording. Commercial builds are also ignored: a version containing `enterprise`
or a standalone `ee` token (for example `5.26-enterprise` or `5.26-ee`) never
appears in multi-version wording, because it is not part of the open-source
offer. Rendered versions keep at most two dotted components (`a.b.c` becomes
`a.b`); one- and two-component versions are unchanged, and duplicate display
versions are removed.

The AWS `ShortDescription` distinguishes multi-version applications: it says
`Choose from <app> <versions> at launch` using every configured version except
`latest` when the application is multi-version and at least two options exist,
otherwise `Deploy <app> <version>`. `{versions.choice}` carries the leading
space, so a missing version source entry renders `Deploy <app> for ...` without
consecutive spaces and remains a draft unless `--publish-ready` is used.
`ShortDescription` does not vary by
`catalog.hasenterprise`; the community/commercial distinction remains in the
long description and pricing copy. In the long description the edition label is
`Community Edition` by default when `hasenterprise` is true and empty otherwise;
an application whose open-source edition has a different name is declared in
`data/listing/app-special-cases.json` (for example `Community Server` or
`Open Source`). A false `hasenterprise` never mentions "Community Edition".
The pricing lead-in comes from `{editions.pricingLead}`, so it repeats the
resolved edition name and uses neutral wording when there is none.
`contentOverrides` provides literal, per-listing replacements for the
title/descriptions/highlights/keywords and still needs review.

## Offer variants in the product title

The application's `multiOffers` in `data/listing/app-special-cases.json`
declares the offer variants published on AWS. An entry that names an operating
system replaces the `on AWS` platform phrase with `on <OS>`; any other entry is
a label inserted after the title trademark. The empty string is the base offer
with no extra label. Several parts combine in the order written.

| Variant | ProductTitle |
| --- | --- |
| `Ubuntu` | `HAProxy on Ubuntu - Preconfigured, Hardened, Ready in Minutes` |
| `Multisite` | `WordPress Multisite on AWS - Preconfigured, Hardened, Ready in Minutes` |
| `Ubuntu; Multisite` | `WordPress Multisite on Ubuntu - Preconfigured, Hardened, Ready in Minutes` |

OS labels are family names without a version, so both Ubuntu offers share the
display name. An OS is recognised by a known name at the start of a part:
Amazon Linux, Oracle Linux, Red Hat, Rocky Linux, AlmaLinux, CentOS, Debian,
Ubuntu, SUSE, SLES, Fedora, Windows Server, FreeBSD and RHEL. Anything else is a
non-OS label.

Only `ProductTitle` varies by offer. `mk offer resolve` detects the variant from
the live title/short description or accepts an explicit `--offer`, then records
`code`, `entityId` and `offer` in the catalog `targets`. `mk offer build`
previews those targets and `mk offer plan` renders the title per target. A
variant change makes an existing reviewed plan stale through `sourceHash`.
`mk offer build --offer-codes` remains available for an explicit preview and
writes the `targets` array even for one code.

A rendered `ProductTitle` over 72 characters does not block `mk offer build`:
the command prints a warning and records `titleWarnings` in the written
document. Use `mk offer titles --cloud aws [--apps <ids>] [--json] [--strict]`
to render titles for every catalog application and list the ones over the
limit; `--strict` makes the command fail instead of only reporting.

Contentful application data, vendor copy and the shared template are the sources
of truth. The tool validates structure and renders deterministically; it does
not maintain a per-application verification status. Correct source data when it
is missing or inconsistent. Unknown Catalog IDs stay null and block publication
until resolved.

## AWS field mapping

| Authored/rendered content | UpdateInformation field |
| --- | --- |
| Title | `ProductTitle` |
| Short description | `ShortDescription` |
| Ordered long-description sections | `LongDescription` |
| 1–3 feature callouts | `Highlights` |
| Intent-oriented keyword phrases | `SearchKeywords` |
| Approved support description | `SupportDescription` |
| Platform-selected category names | `Categories` |
| Public unsigned resource URLs | `LogoUrl`, `VideoUrls`, `AdditionalResources` |
| Promotional images/videos | `PromotionalMedia` (public or presigned S3 URLs) |

`aws-ami.json` declares `SearchKeywords` via a direct placeholder and
`Highlights` via indexed `vendor.highlights` values. It does not update
`Categories`, `LogoUrl`, `VideoUrls`, or `PromotionalMedia`.

`aws-ami-assets.json` is the separate asset template. The current v2 format
(`schemaVersion: 2`, `templateId: aws-ami-assets-v2`) declares `LogoUrl` as text
and `PromotionalMedia` as an ordered list of one to five image entries with
`Type`, `Url`, `Title` and optional `Description`. Text fields support catalog
placeholders, such as `{catalog.id}` in the application installation image URL.
The shared logo and console images do not require catalog logo or screenshots.
The renderer also accepts legacy v1 templates, which render `LogoUrl` from
`{catalog.logo.imageurl}` and up to five images from `{catalog.screenshots}`.
Legacy screenshots may be URL strings or objects with `value`; duplicate URLs
are removed and catalog order is preserved.
The v2 template has one `baseUrl`; LogoUrl/Url use relative paths resolved by
build into complete HTTPS URLs. Existing absolute URL templates remain supported.
The asset template is separate from the copy template. `mk offer plan/apply --assets`
consumes build JSON directly and explicitly refreshes authored fields even when
URLs are unchanged, using the existing revision/hash executor. There is no image
SHA/version staging or S3 byte check. Asset apply requires the reviewed planHash
and defaults to submit-only (no wait); optional positive `--wait-seconds` enables
field read-back without requesting another refresh. See [offer-assets.md](offer-assets.md).
The general AWS media validator accepts presigned URLs, but this asset path
requires unsigned HTTPS URLs so plans do not store signed parameters.
`offer-update.yml` supports assets via its exclusive Asset update type; Product
information remains the default. Neither mode generates or uploads source images.

`aws-ami.json` provides two default Learning resources
(AdditionalResources): `<name> Image Guide by Websoft9` at
`support.websoft9.com/en/docs/<key>`, and `Install Websoft9 on AWS`. A product's
non-empty `listings.aws.additionalResources` overrides those defaults.
`SupportDescription` is the AWS Marketplace Support details field; the two
AdditionalResources entries are the Learning resources rows. The support text
uses LF-separated lines for Docs, Email and SLA. AWS allows `\n` (0x0A) but
rejects other control characters including `\r` (0x0D), so use `\n` only.

`SearchKeywords` is priority-ordered: the leading entries are the ones published
when the list is longer than AWS accepts. Rendering keeps the leading entries
that fit both the 15-entry and 250-combined-character limits, records the
dropped tail as `keywordTruncation` in the preview, and `mk offer build` prints
a warning. An individual keyword over 50 characters is not trimmed and still
fails validation, because dropping a high-priority keyword would hide the
defect instead of fixing it.

Existing entity values are read from `Description`, `PromotionalResources` and
`SupportInformation`; the read shape is not the update shape. The AWS adapter
normalizes `Videos` to `VideoUrls`, `PromotionalMedia` to its writable
`Type`/`Url`/`Title`/`Description`/`PreviewUrl` shape, reads
`SupportInformation.Description` as `SupportDescription`, and removes read-side
additional-resource `Type` fields before comparison. Update payloads use
`Text`/`Url` only for additional resources. Empty/absent optional
categories/media fields preserve existing AWS values; this version does not
implement explicit deletion of them. Categories must be confirmed AWS category
names (AWS also validates them). The local checker validates supported lengths,
duplicates, URL safety and unresolved placeholders. List-level
`SearchKeywords` overflow is trimmed before validation (see above); item-level
limits still fail.
Validation errors name the concrete field path and show an excerpt, for example
`content.ShortDescription contains consecutive spaces: '...'`, so a source-data
problem is not reported only as a generic content error. It cannot establish
semantic correctness or AWS approval.

Deployment instructions and refund policy appear in JSON preview sections, not
the submitted ChangeSet. This avoids treating Usage/Refunds from the example as
`UpdateInformation` fields. The Markdown preview uses plain section headings;
inspect the actual marketplace rendering after a write.

## Commands

The former product-file Issue workflow is paused; see
[`marketplace-offer-update-workflow.md`](marketplace-offer-update-workflow.md).
Application facts now come from the temporary catalog, so a future Issue flow
must write to Contentful instead of a repository product file.

`build` writes the human-review preview; use `--output -` to print it instead.
A draft with missing required inputs still succeeds, records `publishBlockers`
in the preview document, and prints the same blockers as a warning on stderr.
`--publish-ready` fails without writing and reports those blockers verbatim, such
as `versions: version is missing`, instead of a derived formatting symptom. The
default version source is remote; pass a local `--versions` file for a fully
offline run. None of these commands contact AWS.

```sh
mk offer build --app odoo --cloud aws [--offer-codes <code>[,<code>...]] [--allow-unlisted-codes]
mk offer build --app odoo --cloud aws --output /tmp/odoo-preview.md
mk offer build --app odoo --cloud aws --publish-ready
mk offer assets build --app odoo --catalog data/.work/catalog.json --output /tmp/odoo-assets.json
mk offer resolve --app odoo --cloud aws [--code <code>] [--offer <variant>] --profile marketplace-seller
# Or reuse an explicit public store snapshot (no inventory re-query).
mk offer resolve --app odoo --cloud aws --inventory /path/to/inventory.json --visibility public
mk offer plan --app odoo --profile marketplace-seller --output /tmp/odoo-plan.json
python3 ci/validate_offers.py
```

`resolve` consumes public image facts through the shared store service, live or
from `--inventory` store JSON, and uses the [shared deterministic identity
rules](application-matching.md): normalized full trademark, Apache-prefix fallback
and conservative app ID fallback, selecting the first application occurrence.
It loads the full product catalog to detect other applications and exact ties.
Only a title with no catalog match allows
fallback to the summary before its first English/Chinese comma. It does not
read the local offer-code matrix. Public visibility is explicit/default; nonpublic
facts are rejected, and `--profile` cannot accompany `--inventory`.
With `--code`, a code within the public facts is authoritative and a mismatched title is an
override. Without `--code`, each detected offer variant must map to exactly one
code; two codes for the same variant stop the run. `--offer` restricts the
selection to a declared `multiOffers` variant, and combining it with `--code`
forces that variant. The resolved `code`/`entityId`/`offer` targets are written
into the temporary catalog for `build`, `plan` and `apply`.
Every explicit offer choice must belong to the target application's
`multiOffers`; absence does not mean unrestricted selection. Non-OS labels
are recognized from titles, not summary capability claims. `--app` always
accepts one id, never a list.

`plan` refuses to guess when an application has several Catalog targets: pass
`--all-codes` or one or more `--offer-code` values.

`build` is offline and does not contact AWS. It writes
`data/.work/aws/<key>.json` by default (git-ignored) as a human-review preview
that mirrors the authoring template: resolved `content`,
`longDescriptionSections`, `target`, and `AdditionalResources`. By default it
covers the codes recorded in the catalog item; `--offer-codes` previews an
explicit set, and `--allow-unlisted-codes` admits a code recorded under another
application so a forced reassignment is reviewable before `plan`. AWS read/write
views and ChangeSet payloads remain internal program structures. `plan` remains
the live revision-bound step; `--output` saves the complete plan/request for
review and audit.

When catalog targets have already been resolved, `--offer-codes` filters that
set. Unknown/empty filters and `--allow-unlisted-codes` with resolved targets
are errors; run `resolve` again to change the target set. Synchronous
`apply --wait-seconds` verifies each target with its own OS/label rendering.

`--catalog`, `--versions`, `--vendor` and `--template` allow
alternate reviewed local source files. Defaults are
`data/.work/catalog.json`, the configured version source,
`data/listing/vendor.json` and the AWS template.

`mk offer versions --output <path>` snapshots the configured version source so a
plan and its apply reuse identical version data. `mk offer doctor --cloud <cloud>
[--plan-role-arn <arn>] [--apply-role-arn <arn>]` checks that the cloud has a
plan/apply implementation and validates role ARNs before a run.
`mk offer report preflight|resolve|plan|apply --input <path> [--cloud <cloud>]
[--app-name <name>] [--account-id <id>]` renders the same Markdown used for the
GitHub step summary, including the entity ARN for AWS plans; locally it can be
redirected to any review file.

Install optional AWS dependencies and use AWS's normal credential provider
chain (SSO/profile, environment, or assumed role). Do not put credentials in
master data, command arguments, generated plans or repository configuration.

```sh
pip install '.[aws]'
mk offer preflight --app odoo --cloud aws [--offer-codes <code>[,<code>...]] [--allow-unlisted-codes] [--skip-code-check]
mk offer targets --app odoo --cloud aws [--offer-codes <code>[,<code>...]] [--allow-unlisted-codes] [--entity-map <path>]
mk offer plan --app odoo --all-codes --profile marketplace-seller
```

`preflight` validates the application id against `product_en.json`
(`--product-catalog`, also accepted as `--catalog-source`) and every requested
offer code against the published matrix configured by `sources.offer-codes` in
`config/app.json` (`--offer`, also accepted as `--offer-source`), without calling
Contentful or AWS. It reports lowercase and
close-match suggestions. `--skip-code-check` validates only the application id,
which is the mode used before live resolution. `--allow-unlisted-codes` makes
explicit codes authoritative: a code the matrix records under another
application is accepted and listed as `unlistedCodes`, so an existing offer can
be reassigned deliberately. `targets` remains available for an explicit
code-to-EntityId resolution; the apply workflow uses `resolve` instead. It
resolves each code to a seller-owned AMI
Catalog EntityId with pagination and records `targets` in the temporary catalog
item; a public SKU, product code or `prodview-` ID is not a substitute. It
accepts the same flag, but a forced code still has to resolve to exactly one
entity. Codes that resolve to the same EntityId are collapsed into one target and reported
under `duplicates`, so one entity is submitted once. `--entity-map` caches
code-to-EntityId pairs: known codes are re-verified with one `DescribeEntity`
call instead of scanning the whole product catalog. `plan --all-codes` writes one
plan per recorded target, each bound to its own EntityId and base revision. The
integration uses `us-east-1` for the Catalog client.

Save the **reviewed plan output** in an access-controlled location outside Git
(for example under `/tmp/opencode/` for a local trial), then submit exactly it:

```sh
mk offer apply --app odoo --plan /tmp/opencode/odoo-plan.json --profile marketplace-seller --wait-seconds 0
mk offer status <change-set-id> --profile marketplace-seller --wait-seconds 600
```

`apply` is the explicit write action. It rereads live metadata and compares the
complete recomputed plan to the reviewed artifact.
Changed source/template/vendor, live revision or plan payload fails closed.
For a multi-target plan it validates every target before submitting any of them,
then submits one ChangeSet per target. It submits only changed fields, uses the
live `EntityId@RevisionId` and a deterministic plan hash as `ClientRequestToken`.
A no-op plan submits nothing; a partially submitted run reports `PARTIAL` and
exits non-zero, and re-planning skips targets whose content already matches.
AWS locks each entity while it processes that entity's ChangeSet.

`--wait-seconds` bounds polling. Use `0` to submit and exit without waiting; the
workflow uses this mode. For synchronous local verification, set a longer value.
Timeout means the change is still in progress, not that it was cancelled or
failed. `apply` reads back metadata on success when
waiting; `status` only reports the ChangeSet status. Run `plan` again to verify
the desired values after an asynchronously completed submission. Failed or
cancelled terminal states produce a nonzero exit status. Store the plan hash,
source hash, ChangeSet ID and status output as the publishing audit trail.
Raw responses and detailed AWS error messages are not exported, only error codes.

`plan --current <fixture.json>` accepts an offline DescribeEntity-shaped fixture
for development. Do not check real seller API responses into Git. Offline plans
must still match a fresh live snapshot before `apply` will accept them.

## IAM and API boundaries

Read access needs `aws-marketplace:ListEntities`, `DescribeEntity` and
`DescribeChangeSet`. Write access needs `aws-marketplace:StartChangeSet` scoped
to the intended `AmiProduct` ARN and `ChangeSet/*` ARN, with the
`catalog:ChangeType` condition set to `UpdateInformation`. Do not use an
administrator policy for this workflow. SDK calls expose error codes only.

A minimally scoped UpdateInformation statement looks like this (replace the
entity id):

```json
{
  "Effect": "Allow",
  "Action": "aws-marketplace:StartChangeSet",
  "Resource": [
    "arn:aws:aws-marketplace:us-east-1:797851739507:AWSMarketplace/AmiProduct/prod-REPLACE_ME",
    "arn:aws:aws-marketplace:us-east-1:797851739507:AWSMarketplace/ChangeSet/*"
  ],
  "Condition": {
    "StringEquals": {
      "catalog:ChangeType": "UpdateInformation"
    }
  }
}
```

Use `AmiProduct`, not `AmiProduct@1.0`, in the ARN. The target entity ARN is
printed in the plan summary. An `AccessDeniedException` from `StartChangeSet`
usually means the action is missing, the entity/ChangeSet ARN does not match,
the condition key or change type is wrong, or an SCP/permissions boundary
denies the write.

The managed policies `AWSMarketplaceSellerOfferManagement` and
`AWSPrivateMarketplaceRequests` are not sufficient for this workflow. The
former allows `StartChangeSet` only for `Offer/*` and `ChangeSet/*` (plus
`CreateOfferOnProduct`); the latter covers procurement requests. Add an inline
or customer-managed policy for `AmiProduct/*` with the `catalog:ChangeType`
condition shown above.

The API's `Intent: VALIDATE` is documented for adding single-AMI versions;
it is not a general dry-run for `UpdateInformation`. Local `validate` and
`plan` therefore perform no StartChangeSet call. AWS may reject content
asynchronously even after local validation passes.

MCP is optional. The official AWS MCP Server provides generic AWS API access,
but publication automation uses the deterministic SDK adapter here. A future
MCP wrapper should call the same render/plan/apply logic, preserving IAM and
revision checks rather than maintaining separate payload generation rules.

## Source references (checked 2026-11-03)

- [Seller products / UpdateInformation](https://docs.aws.amazon.com/marketplace-catalog/latest/api-reference/seller-products.html#update-information)
- [StartChangeSet](https://docs.aws.amazon.com/marketplace-catalog/latest/api-reference/API_StartChangeSet.html)
- [DescribeEntity](https://docs.aws.amazon.com/marketplace-catalog/latest/api-reference/API_DescribeEntity.html)
- [DescribeChangeSet](https://docs.aws.amazon.com/marketplace-catalog/latest/api-reference/API_DescribeChangeSet.html)
- [Catalog IAM access](https://docs.aws.amazon.com/marketplace/latest/developerguide/catalog-api-access-control.html)
- [AWS-maintained AMI shapes](https://github.com/awslabs/aws-marketplace-catalog-api-shapes-for-python)
- [Official AWS MCP Server](https://docs.aws.amazon.com/agent-toolkit/latest/userguide/mcp-server.html)

AWS documentation and generated shapes can disagree (for example Sku limits);
unsupported fields are excluded from this adapter. Recheck documentation when
expanding its field/change-type scope.
