---
name: app-content-push
description: Push validated application fields from data/.work/catalog.json to Contentful with mk catalog push, using a dry run first and drafts by default.
---

# Application Content Push

## Scope

Push content that is already present in `data/.work/catalog.json` to
Contentful. This skill does not generate or rewrite content; use
`app-content-gen` for that.

CLI owns input validation, schema/link resolution, field/locale merging,
version-conflict handling and API writes. This skill owns selection of the
intended apps/fields, draft versus publish authorization, review of dry-run
results and reporting. CLI validation does not prove factual accuracy; do not
invent a second validator or rewrite content to make push pass.

## Inputs

| Input | Required | Values |
| --- | --- | --- |
| `apps` | yes | Comma-separated ids such as `odoo,wordpress`, or `all` |
| `fields` | no | Comma-separated fields to push: `trademark`, `overview`, `keywords`, `usecases`, `relatedApps`, `summary`, `hasenterprise`; default `overview,keywords,usecases,relatedApps,summary,hasenterprise`. Pass `trademark` explicitly to change the product name |
| `publish` | no | `true` publishes each updated entry; default `false` (drafts only) |
| `preview` | no | `true` stops after the dry run; default `false` |

If `apps` is missing, print the accepted values and exit without writing.

### Default field scope

When `fields` is omitted, push
`overview,keywords,usecases,relatedApps,summary,hasenterprise`. The reasons:

- These six are exactly the fields `app-content-gen` owns and can change.
- `trademark` is product identity, not generated copy; changing it is a
  deliberate rename, so it requires an explicit `fields=trademark`.
- `id` is only a Contentful lookup key and `offerCodes` is pull metadata; neither
  is pushed.
- Even inside the default set, the command writes only fields that are present,
  non-empty and different from Contentful. Empty values are skipped, and
  unchanged values are no-ops.

Pass the same `fields` that were regenerated whenever they are known; the
default is a full content sync, not a per-run diff.

## Preflight

- Run commands from the repository root.
- `mk` is usually not on `PATH`. Use `make cli ARGS="..."`, or prefix
  `PATH="$PWD/.venv/bin:$PATH"` once.
- Push requires `CONTENTFUL_SPACE_ID` and a Management API token in
  `CONTENTFUL_TOKEN` (`CFPAT-`). A Delivery/Preview token returns HTTP 401.
  Never print, echo or store the token.
- `data/.work/catalog.json` is git-ignored scratch data and must exist.
- Resolve `<catalog>` once, honoring `$MK_CATALOG_DIR/catalog.json` when set,
  and pass `--input <catalog>` to every command.
- Every requested id must exist in the catalog. If an id is missing, stop and
  report that `app-content-gen` must prepare the data first. Do not pull
  here: a pull would overwrite freshly generated content.

## Workflow

Resolve `fields` once: use the input value when supplied, otherwise use
`overview,keywords,usecases,relatedApps,summary,hasenterprise`. Pass the resolved
list to every command so the default is explicit.

### 1. Validate before writing

An optional diagnostic check uses the push contract, not generation policy:

```sh
make cli ARGS="catalog verify --input <catalog> --apps <apps> --fields <resolved-fields> --profile push --verbose"
```

Prefer the same `fields` that were regenerated: the catalog may contain
unrelated empty or legacy values that are not part of this push. If it reports
errors, stop and send the result back to `app-content-gen`; do not push invalid
content. Every `catalog push`, including dry runs, also validates its exact
in-memory input before platform access. Absent and empty fields are permitted;
store filtering and linked-entry resolution remain CLI/platform responsibilities.

### 2. Dry run

```sh
make cli ARGS="catalog push --input <catalog> --apps <apps> --fields <resolved-fields> --dry-run --verbose"
```

The dry run prints selected, matched, would-update, unchanged and skipped-empty
counts plus per-application changes. If it reports errors or a non-zero exit,
stop and report the exact output. If `preview=true`, stop here.

### 3. Push drafts

```sh
make cli ARGS="catalog push --input <catalog> --apps <apps> --fields <resolved-fields> --verbose"
```

Add `--publish` only when `publish=true`.

The command updates drafts by default. It writes only values that differ from
Contentful, skips empty values unless `--clear` is used (do not use `--clear`
unless explicitly requested), and exits non-zero if any application fails.
Never claim success on a non-zero exit. Report the summary counts and every
per-application error unchanged.

### 4. Confirm the write

When practical, run the dry run once more:

```sh
make cli ARGS="catalog push --input <catalog> --apps <apps> --fields <resolved-fields> --dry-run --verbose"
```

Expect `Would update: 0` after draft or published updates. A remaining update
requires investigation (failed persistence or a concurrent edit); do not blindly
repeat the write. Publishing updates and publishes the new version; it does not
intentionally leave that version as a pending draft.

## Field notes

- `trademark` is excluded from the default scope; pass it explicitly only for a
  deliberate rename.
- A field absent from a catalog item is skipped, so partial items are safe.
- `relatedApps` is sent as Trademarks. `mk catalog push` drops names that are
  not sold in this app store, removes duplicates, and fails the application on
  an allowed but unknown or ambiguous Trademark. It never truncates a longer
  human-curated list.
- `hasenterprise` is a JSON Boolean. `false` is written as a real value;
  `--clear` is not needed.
- `--publish` cannot be combined with `--dry-run`.
- Push writes only the selected fields for the selected applications. Other
  entries, locales, tags, references and unknown fields are preserved.

## Report

Use this table, one row per selected application:

| app | fields | dry run | push | note |
| --- | --- | --- | --- | --- |

`push` is `draft`, `published`, `unchanged`, `failed` or `not attempted`.
Include the command exit status and any skipped or missing ids. End with the
exact summary counts from `mk catalog push`.

## Stop without writing

- Catalog file missing, malformed, or a requested id absent.
- `catalog verify` reports errors.
- Dry run reports errors or exits non-zero.
- `CONTENTFUL_TOKEN` is missing or is not a Management token.
- Any push command exits non-zero: report it; do not retry blindly.
