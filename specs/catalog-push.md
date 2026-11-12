# Catalog push to Contentful

`mk catalog push` writes English application metadata from a JSON dataset to
Contentful through the **Management API**. It updates existing `product` entries
matched by their `key` field. It does not create entries, delete entries, change
content models or edit assets.

## Credentials

Push uses a Contentful **Management API token**, not the Delivery/Preview token
used by `catalog pull`:

```sh
export CONTENTFUL_SPACE_ID=...
export CONTENTFUL_TOKEN=...              # CFPAT- Management API token
```

The same `CONTENTFUL_TOKEN` variable is used by pull and push. Push requires a
Management token (`CFPAT-`); a Delivery/Preview token cannot write and returns
HTTP 401. The token is sent in the `Authorization` header and is never printed.

## Usage

```sh
# Preview changes; no Contentful writes, with a field-by-field diff
mk catalog push --apps wordpress,odoo --dry-run --json --no-progress

# Push only the regenerated fields
mk catalog push --apps wordpress,odoo --fields summary,keywords --dry-run --verbose

# Update drafts only
mk catalog push --apps wordpress,odoo

# Update and publish
mk catalog push --apps wordpress,odoo --publish
```

Every push invocation, including a dry run, validates the exact loaded input
before platform access with the mechanical `push` validation profile; safety does
not depend on a separate verification step. Validation failure stops that
invocation before remote requests or writes. Generation-specific editorial policy
is not re-applied to historical content during push.

`--input` defaults to `data/.work/catalog.json` (git-ignored). Set
`MK_CATALOG_DIR` to use another directory. It accepts the object produced by
`catalog pull` (`items`), a bare JSON array, or `-` for stdin. `--apps` defaults
to every item in the input and preserves the requested order. Single-app and
multi-app data use the same file shape; only the number of `items` changes.

## Comparison and JSON output

`--json` writes one machine-readable result to stdout while progress and errors
remain on stderr. It includes the existing counts and an `errors` array, plus a
`changes` array with one entry per application that would change:

```json
{
  "selected": 1,
  "matched": 1,
  "updated": 1,
  "published": 0,
  "unchanged": 0,
  "skippedEmpty": 0,
  "missing": [],
  "errors": [],
  "dryRun": true,
  "changes": [
    {
      "app": "wordpress",
      "fields": [
        {
          "field": "summary",
          "before": {"present": true, "value": "Old summary"},
          "after": {"present": true, "value": "New summary"}
        }
      ]
    }
  ]
}
```

`before` is the entry's current value for the selected locale; `after` is the
value that would be written. `present: false` means the field or locale was
absent, which is distinct from a present `null`, `""` or `[]`. Text and Boolean
values are returned as-is, arrays as JSON arrays, and `relatedApps` as application
Trademarks; a link that cannot be mapped back to a sold application is shown as
`<link:ENTRY_ID>`. `changes` is populated for both dry runs and writes.

## Field mapping and merge

| Input field | Contentful field | Scope |
| --- | --- | --- |
| `id` | `key` | Lookup only; never updated |
| `trademark` | `trademark` | `--locale` (default `en-US`) |
| `overview` | `overview` | `--locale` |
| `keywords` | `keywords` | `--locale` |
| `usecases` | `useCases` | Non-localized |
| `relatedApps` | `relatedApps` | Non-localized |
| `summary` | `summary` | `--locale` |
| `hasenterprise` | `hasEnterprise` | Non-localized |

The field IDs and localization flags come from the Contentful content-type
schema. The full entry is fetched first and only the target locale/fields are
merged; other locales, `tags`, references and unknown fields are preserved.
Input fields outside the table are rejected instead of silently ignored, except
pull metadata such as `offerCodes`, which is ignored. `--fields` restricts the
push to a comma-separated subset of the table, so a generation pass that
changed only `summary` cannot accidentally write stale values from other
fields. Unselected fields are left untouched in Contentful.

`relatedApps` input is an array of application Trademarks. Push validates it
against the store catalog: names that are not sold in the store are dropped,
duplicates are removed, and the result is not truncated. Remaining Trademarks
are resolved to linked `product` entries by `trademark`; an allowed but unknown
or ambiguous Trademark fails that application instead of writing a wrong link.
Only the store products' `sys.id` and `fields.trademark` are fetched, once per
push.

## Empty values and clearing

Empty strings, empty arrays and `null` are skipped by default. This prevents a
`pull` dataset containing `"usecases": []` from clearing a populated field.
Boolean `false` is a real value and is written. Use `--clear` to explicitly
write `""` for text fields, `[]` for array fields or `false` for Boolean fields.

## Safety and reporting

- Entry lookup is filtered by the selected application keys (`fields.key` /
  `fields.key[in]`), so a push for a few apps does not fetch the whole product
  table.
- Only entries whose target values differ are written.
- Updates send the fetched entry's `X-Contentful-Version`; a concurrent edit
  returns HTTP 409 and is reported instead of overwritten.
- Default behavior updates **drafts**. `--publish` publishes each successfully
  updated entry using the new draft version. It cannot be combined with
  `--dry-run`.
- `--allow-missing` skips input applications absent from Contentful; otherwise
  they are errors.
- The command continues through independent application errors and exits
  non-zero if any occurred. Summary counts and per-app details go to stderr;
  `--verbose` shows changed field names, and `--json` emits counts, errors and
  per-field before/after values on stdout.
- `--progress` shows schema/entry pagination progress; `--no-progress` disables it.

## Limitations

- English locale only per invocation (`--locale`).
- Existing `product` entries only; no create/delete.
- `tags` and other linked fields except `relatedApps` are not pushed.
- Factual accuracy is not validated; the input dataset must be reviewed.
- Dry-run review and draft/publish authorization remain skill responsibilities.
  A separate earlier dry run is not a server-side transaction or authorization
  token; another edit between dry run and push can change the proposed diff.
  Version headers protect against edits after the entry is fetched for writing.

## Validation

```sh
python3 -m unittest tests.test_catalog_push tests.test_catalog_verify
```

Tests use mocked Management API responses. A live push requires a Management API
token with write access to the product entries. A Delivery token cannot perform
this operation.
