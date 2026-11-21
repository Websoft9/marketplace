# Catalog pull from Contentful

`mk catalog pull` exports one JSON dataset for the applications that already
exist in `product_en.json`. Contentful owns the English application fields;
`product_en.json` is only the target application list, not the metadata source.
This command does not write Contentful, AWS or marketplace data. Use
[`catalog-push.md`](catalog-push.md) to write reviewed metadata back through the
Contentful Management API.

## Usage

Prefer environment variables so the token is not stored in shell history:

```sh
export CONTENTFUL_SPACE_ID=...
export CONTENTFUL_TOKEN=...             # Delivery/Preview token, or a CFPAT- Management token
mk catalog pull                         # writes data/.work/catalog.json
mk catalog pull --output -              # write JSON to stdout
```

A single `CONTENTFUL_TOKEN` variable is used by both pull and push. A `CFPAT-`
Management token is automatically read through the Management API, so the same
Management token can query and update. A Delivery/Preview token uses the CDA and
is read-only. Mixed token types cannot be used with the other command.

The default staging file is `data/.work/catalog.json`, which is
git-ignored. Set `MK_CATALOG_DIR` to use another directory. This one file holds
both single-app and multi-app results; `items` length is the selected application
count. Use `--output` only when you need an additional snapshot.

The application filter is applied in the Contentful query (`fields.key` for one
app, `fields.key[in]` for multiple), so selecting one app fetches one entry rather
than the whole product table. Even with no `--apps`, only the `product_en.json`
target keys are queried.

Export only selected applications (request order is preserved):

```sh
mk catalog pull --apps wordpress,odoo --output /tmp/catalog-subset.json
```

Merge selected applications into an existing staging file without removing the
other applications:

```sh
mk catalog pull --apps wordpress,odoo --merge
```

`--merge` replaces the matching items and preserves every other item and the
existing order. Without `--merge`, `--apps` writes a dataset that contains only
the selected applications.

Merge replaces **whole matching items**, including local fields not returned by
pull; it is not a selected-field merge. Explicit refresh can discard unpushed
generated content and local listing metadata for those items. Every file pull
retains exact previous bytes in a unique sibling `catalog.json.before-*.json`
backup before atomic replacement. CLI writers coordinate with a sibling lock;
changes since the initial read cause refusal rather than an overwrite. Stdout
pull does not create backups or locks. Scratch backups/locks must not be committed.

Preview API example:

```sh
mk catalog pull --space-id "$CONTENTFUL_SPACE_ID" --token "$CONTENTFUL_PREVIEW_TOKEN" \
  --api-url https://preview.contentful.com --output /tmp/catalog-preview.json
```

The token is sent in the `Authorization` header and is never printed or included
in the dataset. Delivery/Preview tokens read published/preview content through
the CDA. `CFPAT-` Management tokens read through the Management API, which also
includes drafts; linked fields may remain as link objects in that mode.

## Defaults and options

| Option | Default | Purpose |
| --- | --- | --- |
| `--space-id` | `CONTENTFUL_SPACE_ID` | Contentful space |
| `--token` | `CONTENTFUL_TOKEN` | Delivery/Preview token, or a `CFPAT-` Management token |
| `--environment` | `master` | Contentful environment |
| `--content-type` | `product` | Content type id |
| `--locale` | `en-US` | English locale |
| `--id-field` | `key` | Contentful field containing the app id (`wordpress`) |
| `--fields` | `trademark,overview,keywords,usecases,relatedApps,summary,hasenterprise,description` | Requested field ids |
| `--catalog` | configured `product_en.json` URL | Target application list (URL or local file) |
| `--offer` | `sources.offer-codes.url` in `config/sources.json` | Published offer-codes artifact URL, local JSON file, or directory used to add per-platform `offerCodes` |
| `--skip-offer-codes` | off | Do not read the offer-codes matrix; the apply workflow resolves codes live |
| `--apps` | all catalog applications | Comma-separated application ids to export; must exist in the target catalog |
| `--output` | `data/.work/catalog.json` | JSON destination; `-` means stdout; `MK_CATALOG_DIR` overrides the directory |
| `--merge` | off | Merge pulled items into an existing output file, preserving other items and their order |
| `--progress` | on | Progress to stderr, leaving stdout valid JSON |
| `--raw` | off | Keep raw Contentful values instead of resolving links/RichText |
| `--allow-missing` | off | Emit available apps and list target ids absent from Contentful |
| `--page-size` | 100 | Contentful pagination size (1–1000) |
| `--include-depth` | 1 | Contentful `include` depth for linked entries/assets |
| `--timeout` | 30 | HTTP timeout in seconds |

`usecases` also matches the Contentful field id `useCases` (and `use_cases`).
`relatedApps` maps to the Contentful `relatedApps` field, a linked `product`
array resolved to each entry's Trademark. Results are filtered to applications
present in the target catalog, removing self-references and duplicates; use
`--raw` to keep the unfiltered links.
`hasenterprise` also matches `hasEnterprise` and `has_enterprise`.
`description` is the Contentful **Description** Markdown text field (API id
`description`; field-id matching also accepts `Description`). Default pulls
include it as the local `description` key. Markdown headings, links, code blocks,
line breaks and whitespace are preserved as a JSON string without rendering or
plain-text conversion. An unset value is emitted as `null`.
`keywords` is the default for search/tag-like metadata; the older `tags` field is
still available explicitly via `--fields` and also matches `tag`. Use `--fields`
for any other content-model names.

The default offer-code source is the published artifact URL in
`config/sources.json` (`sources.offer-codes.url`); a local `--offer` value can be used to test staged data.

## Output

Only applications present in `product_en.json` are returned. Without `--apps`,
catalog order is used; with `--apps`, the requested order is used. With
`--merge`, existing items keep their positions and new items are appended in
pulled order:

```json
{
  "schemaVersion": 1,
  "generatedAt": "2026-11-05T00:00:00Z",
  "count": 138,
  "items": [
    {
      "id": "wordpress",
      "trademark": "WordPress",
      "overview": "...",
      "keywords": ["open source", "website"],
      "usecases": [],
      "relatedApps": ["PostgreSQL", "pgAdmin"],
      "summary": "...",
      "hasenterprise": false,
      "offerCodes": {
        "aws": ["wp-example-code"],
        "azure": ["publisher.offer"]
      }
    }
  ],
  "source": {
    "catalog": "https://artifact.websoft9.com/appstore/release/catalog/product_en.json",
    "offer": "https://artifact.websoft9.com/cloud_data/offer.json",
    "contentful": {
      "api": "https://cdn.contentful.com",
      "spaceId": "...",
      "environment": "master",
      "contentType": "product",
      "locale": "en-US"
    }
  }
}
```

Fields that exist in the Contentful content model but are empty are normal query
results: array fields are emitted as `[]`, Boolean fields as `false` except
`hasenterprise` (missing/null defaults to `true`), other fields as `null`, and
counts are reported to stderr. No extra flag is required.

`logo` and `screenshots` are copied from `product_en.json` when Contentful does
not expose an image URL (Contentful's `logo` field is text in the current
content model). This keeps the asset source available to
`mk offer assets build` without adding copy fields to the asset template.
A requested field id that does not exist in the content model is still rejected
so `--fields` typos fail loudly.

By default a target application absent from Contentful still fails the export;
`--allow-missing` writes the available records and adds a `missing` array.
With merge, missing ids from this pull are retained in that array even when an
old local item remains; recovered ids are removed and unrelated prior missing
ids are preserved. Item presence alone does not prove a successful refresh.

`offerCodes` maps the configured offer-codes artifact's `productID` values by platform.
It is copied from the JSON array returned by the URL (or from local JSON files,
merged by application id, in filename order) when the app id or one of its
`applications` matches; apps without an offer entry receive `{}`. Array values such as
`keywords` and `usecases` are rendered on one line
(`["open source", "website"]`), while records and objects remain indented.
Linked entries/assets and RichText documents are converted to readable values;
use `--raw` to preserve the exact CDA shape.

## Validating the dataset

`mk catalog verify` checks the field rules used by the content skills and can
fail on unintended edits:

```sh
mk catalog verify --apps wordpress,odoo --fields keywords,summary --verbose
mk catalog verify --apps wordpress,odoo --fields summary \
  --baseline /tmp/catalog.before.json
mk catalog verify --apps wordpress,odoo --fields relatedApps \
  --reference product_en.json
mk catalog verify --apps wordpress,odoo --fields description
```

The baseline form verifies that no other application and no unselected field
changed. `--require-non-empty` treats empty `keywords` and `usecases` as errors;
use it after generation, not when checking a partial catalog. When the staging
catalog is partial, pass `--reference product_en.json` to validate `relatedApps`
against the store catalog instead of the Trademarks in the input file.
`catalog apply` accepts the same option. `relatedApps` is also checked for
repeated layers, such as multiple relational data stores,
database admin tools, model runtimes, vector stores or SCM tools; these are
warnings because universal clients and data pipelines can legitimately span
layers. Summaries that contain `open source`/`open-source` are errors. Add
`--json` for a machine-readable result. Errors exit non-zero; warnings are
reported but do not block.

`description` is opt-in for verify/apply; the default verification field list is
unchanged so older staging files without it remain compatible. Explicit
verification accepts non-blank Markdown strings without overview length,
punctuation or surrounding-whitespace restrictions. The `push` profile also
permits absent/null/empty descriptions, matching push skip/clear behavior.
`catalog apply --fields description` uses the usual isolation, backup and atomic
commit protections and does not change Markdown source text.
With `--profile generated`, descriptions are limited to 150 whitespace-separated
Markdown-source tokens in total, including optional lists and standalone markup.
The limit does not apply to pull, existing-content validation or push. Selected
descriptions may be generated from missing or null values and committed through
`catalog apply`; the final candidate must contain a non-blank string. The skill
reviews substantive prose (required), optional lists, app-specific features,
distinctive capabilities and problems solved. Ecosystem maturity may be described
qualitatively, without precise popularity metrics such as GitHub star counts.

### Validation profiles and ownership

`--profile existing` is the compatible default. `--profile generated` applies
the generated-content checks implemented by `cli/catalog_verify.py` to the fields
being regenerated; it does not truncate existing values. Use it only for
regenerated fields. `--profile push` permits missing/empty fields, validates
non-empty selected values and leaves relationship filtering, deduplication and
linked-entry resolution to the push adapter. Explicit clears remain supported by
push; empty fields are not new-content errors in the push profile. JSON `checked`
statuses distinguish errors, warnings and skipped-empty fields; use `issues` for
details.

The exact generated-field formatting and editorial rules live with the
[`app-content-gen` field rules](../.agents/skills/app-content-gen/references/field-rules.md).
All profiles are mechanical, not factual review. Product roles, official evidence,
edition boundaries, English language, editorial AI requirements, stack order,
real integrations, competitors and justified layer exceptions remain skill
responsibilities. Layer conflicts, hype detection and repeated summary/overview
are advisory warnings, not automatic semantic decisions.

### Applying generated candidates

`catalog apply` commits a validated candidate against a baseline without copying
a generated catalog over the staging file. `--input` defaults to the same staging
path as pull/verify, including `MK_CATALOG_DIR`. `--apps` and `--fields` must be
explicit; `all` selects all local items. Candidate and baseline must be separate
from the destination, and the current staging data must equal the baseline.

The command rejects changed metadata, item set/order, unselected items/fields
(including missing versus null), malformed/duplicate/missing ids, and a stale
baseline regardless of `--skip-invalid`. Without `--skip-invalid`, any validation
error prevents a commit. With it, only selected-app content errors are
recoverable: the failed app is restored in full from baseline, surviving apps are
verified again, and the result is written with an exact backup and atomic
replacement. No changes means no replacement. JSON output includes
succeeded/skipped ids, actual changed fields, validation issues, backup and output
paths. Exit zero with skipped apps is partial success, not success for every
requested app.

Locks coordinate CLI writers on Linux; external editors must not write during
commit. Pre-commit external changes are detected, but arbitrary external writes
are not prevented. Batch scheduling, semantic correction, evidence gathering and
resume selection remain skill responsibilities; CLI does not generate content,
fetch evidence, classify roles or offer unattended resume.

## Validation

```sh
python3 -m unittest tests.test_catalog_pull tests.test_catalog_verify tests.test_catalog_apply
```

Tests use synthetic Contentful responses. A live run requires a valid Contentful
space, environment, token and content type; the test space returns HTTP 404.
