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
mk catalog pull                         # writes data/.catalog/catalog.json
mk catalog pull --output -              # write JSON to stdout
```

A single `CONTENTFUL_TOKEN` variable is used by both pull and push. A `CFPAT-`
Management token is automatically read through the Management API, so the same
Management token can query and update. A Delivery/Preview token uses the CDA and
is read-only. Mixed token types cannot be used with the other command.

The default staging file is `data/.catalog/catalog.json`, which is
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
| `--fields` | `trademark,overview,keywords,usecases,relatedApps,summary,hasenterprise` | Requested field ids |
| `--catalog` | configured `product_en.json` URL | Target application list (URL or local file) |
| `--offer` | configured `offer.json` URL | Marketplace offer source used to add per-platform `offerCodes` |
| `--apps` | all catalog applications | Comma-separated application ids to export; must exist in the target catalog |
| `--output` | `data/.catalog/catalog.json` | JSON destination; `-` means stdout; `MK_CATALOG_DIR` overrides the directory |
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
`keywords` is the default for search/tag-like metadata; the older `tags` field is
still available explicitly via `--fields` and also matches `tag`. Use `--fields`
for any other content-model names.

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
A requested field id that does not exist in the content model is still rejected
so `--fields` typos fail loudly.

By default a target application absent from Contentful still fails the export;
`--allow-missing` writes the available records and adds a `missing` array.

`offerCodes` maps the configured offer source's `productID` values by platform.
It is copied from `offer.json` when the app id or one of its `applications`
matches; apps without an offer entry receive `{}`. Array values such as
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
```

The baseline form verifies that no other application and no unselected field
changed. `--require-non-empty` treats empty `keywords` and `usecases` as errors;
use it after generation, not when checking a partial catalog. `relatedApps` is
also checked for repeated layers, such as multiple relational data stores,
database admin tools, model runtimes, vector stores or SCM tools; these are
warnings because universal clients and data pipelines can legitimately span
layers. Summaries that contain `open source`/`open-source` are errors. Add
`--json` for a machine-readable result. Errors exit non-zero; warnings are
reported but do not block.

## Validation

```sh
python3 -m unittest tests.test_catalog_pull tests.test_catalog_verify
```

Tests use synthetic Contentful responses. A live run requires a valid Contentful
space, environment, token and content type; the test space returns HTTP 404.
