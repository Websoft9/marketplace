---
name: app-content-push
description: Push validated application catalog changes to Contentful. Run a dry run, show a field-by-field before/after comparison, and ask once whether to update drafts, update and publish in one step, or cancel. Never publishes without an explicit decision.
---

# Application Content Push

## Scope

Push content that is already present in `data/.work/catalog.json` to
Contentful. This skill does not generate or rewrite content; use
`app-content-gen` for that.

CLI owns input validation, schema/link resolution, field/locale merging,
version-conflict handling, remote before/after data and API writes. This skill
owns selection of the intended apps/fields, comparison review, the single
draft-versus-publish decision, and reporting. CLI validation does not prove
factual accuracy; do not invent a second validator or rewrite content to make
push pass.

## Inputs

| Input | Required | Values |
| --- | --- | --- |
| `apps` | yes | Comma-separated ids such as `odoo,wordpress`, or `all` |
| `fields` | no | Comma-separated fields to push: `trademark`, `overview`, `keywords`, `usecases`, `relatedApps`, `summary`, `hasenterprise`; default `overview,keywords,usecases,relatedApps,summary,hasenterprise`. Pass `trademark` explicitly to change the product name |
| `publish` | no | `ask` shows the comparison and asks for a decision; `false`/`draft` pre-authorizes drafts only; `true`/`publish` pre-authorizes update and publish. Default `ask` |
| `preview` | no | `true` stops after the comparison; default `false` |

If `apps` is missing, print the accepted values and exit without writing.
A requested mode that conflicts with `preview=true` stops without writing.

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
- Create a unique run directory under `/tmp/opencode` or an approved `$TMPDIR`
  for dry-run/result JSON and any comparison artifact; never use shared fixed
  temporary filenames.
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

### 2. Dry run and build the comparison

Run the authoritative remote dry run and capture its JSON separately from
progress/errors:

```sh
make cli ARGS="catalog push --input <catalog> --apps <apps> --fields <resolved-fields> --dry-run --json --no-progress" > <run>/push-dry-run.json
```

Inspect the exit status and the JSON `errors` array. The JSON is emitted before
the command's non-zero exit for application errors, so parse stdout when it is
valid JSON and report stderr as well. If the command fails or reports errors,
stop and report the exact output; do not write. The dry run's `changes` array is
the authoritative comparison at that moment:

- Each entry has `app` and `fields`; each field has `field`, `before` and `after`.
- `before`/`after` are `{ "present": <boolean>, "value": <value> }`.
  Render `present: false` as `<missing>`; preserve `null`, `""` and `[]` as
  distinct values.
- For `relatedApps`, values are Trademarks. An unresolved link is shown as
  `<link:ENTRY_ID>`; do not hide it or treat it as a valid relationship.
- Show every changed field for every app, grouped by app id/trademark, using
  complete values. Do not truncate `overview` or `summary`; render arrays as
  JSON. Escape table separators or use code blocks so values cannot break the
  report.
- If there are no changes, report that Contentful is already up to date, do not
  ask a push question, and stop.
- If `preview=true`, stop after presenting the comparison; do not ask or write.

If the change set is too large for one table, write the complete comparison to
`<run>/push-diff.md`, include its path, and still show every app and changed
field with full before/after values in the report or linked section. A shortened
preview alone is not sufficient for the draft/publish decision.

### 3. One decision point

Present the comparison before asking. Unless the current request already
explicitly authorizes a specific write mode, ask one question with all three
options:

1. **仅更新草稿（不发布）** — recommended default.
2. **更新并发布** — update and publish in one step.
3. **暂不更新** — cancel without writing.

The update-and-publish option must always be present; never ask only about
drafts. English equivalent: "Update drafts only", "Update and publish", "Do not
update". Use the user's language.

- An explicit `publish=true`/`publish` pre-authorizes option 2; still show the
  comparison and proceed without downgrading to drafts. Do not re-ask unless the
  request is ambiguous.
- An explicit `publish=false`/`draft` pre-authorizes option 1. Do not treat the
  absence of a `publish` value as draft authorization; default `ask`.
- If the user declines or the decision is unclear, stop without writing. Never
  choose publish on the user's behalf.

### 4. Execute the selected write

For drafts only:

```sh
make cli ARGS="catalog push --input <catalog> --apps <apps> --fields <resolved-fields> --json --no-progress" > <run>/push-result.json
```

For update and publish in one step:

```sh
make cli ARGS="catalog push --input <catalog> --apps <apps> --fields <resolved-fields> --publish --json --no-progress" > <run>/push-result.json
```

The command updates only values that differ from Contentful, skips empty values
unless `--clear` is used (do not use `--clear` unless explicitly requested), and
exits non-zero if any application fails. Never claim success on a non-zero exit.
Report the summary counts, per-application errors and `published` count unchanged.
Do not combine `--publish` with `--dry-run`.

### 5. Confirm the write

When practical, run the dry run once more with `--json`:

```sh
make cli ARGS="catalog push --input <catalog> --apps <apps> --fields <resolved-fields> --dry-run --json --no-progress" > <run>/push-confirm.json
```

Expect `updated: 0` and `changes: []`. A remaining change or error means the
write did not fully persist or a concurrent edit occurred; report it and do not
blindly repeat the write.

## Field notes

- `trademark` is excluded from the default scope; pass it explicitly only for a
  deliberate rename.
- A field absent from a catalog item is skipped, so partial items are safe.
- `relatedApps` is sent as Trademarks. `mk catalog push` drops names that are
  not sold in this app store, removes duplicates, and fails the application on
  an allowed but unknown or ambiguous Trademark. It never truncates a longer
  human-curated list. The comparison shows `<link:...>` for unresolved links.
- `hasenterprise` is a JSON Boolean. `false` is written as a real value;
  `--clear` is not needed.
- `--publish` cannot be combined with `--dry-run`.
- Push writes only the selected fields for the selected applications. Other
  entries, locales, tags, references and unknown fields are preserved.
- The dry-run comparison proves the remote diff only at dry-run time; a later
  concurrent edit can change it. Version headers protect the eventual write, not
  the earlier review.

## Report

Group the before/after comparison by app, then use this outcome table:

| app | fields | decision | result | note |
| --- | --- | --- | --- | --- |

`decision` is `draft`, `publish`, `preview` or `not attempted`; `result` is
`draft updated`, `published`, `unchanged`, `failed` or `not attempted`. Include
the command exit status and any skipped or missing ids. End with the exact
summary counts from `mk catalog push`, including `Published` when applicable and
any remaining `Would update` count after confirmation.

## Stop without writing

- Catalog file missing, malformed, or a requested id absent.
- `catalog verify` or the dry run reports errors or exits non-zero.
- No changes are present; report up to date without asking to push.
- `CONTENTFUL_TOKEN` is missing or is not a Management token.
- The draft/publish decision is missing, declined or ambiguous.
- Any push command exits non-zero: report it; do not retry blindly.
