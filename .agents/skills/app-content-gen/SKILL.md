---
name: app-content-gen
description: Use when refreshing or improving English application metadata in the Contentful catalog, including product summaries and overviews, search keywords, use cases, complementary app relationships, or commercial-edition facts. Gather official evidence, generate selected fields, and safely stage local changes through the CLI. Also supports pull-only requests. Does not push to Contentful, draft full marketplace listings, or check marketplace status and versions.
---

# Application Content Gen

## Scope

Pull catalog data, or pull and regenerate selected English listing fields, for
selected applications in the staging catalog (default `data/.work/catalog.json`,
or `$MK_CATALOG_DIR/catalog.json`). In the generation stage, after any explicitly
requested refresh or missing-item pull,
preserve every other application, every unselected field, `schemaVersion`,
`generatedAt`, `count`, `source` and item order. In pull-only mode, refresh the
selected items and let the pull update `generatedAt` and `count`. Relationship
candidates come from the store catalog (`product_en.json`), not from the
staging file.

Resolve the requested intent and fields before choosing a mode. Explicit
natural-language generation requests can supply the field selection even when
the `fields` parameter is omitted. Only a pure pull request or a request without
generation intent uses standalone pull-only mode. An ambiguous generation
request must stop without writing, not silently fall back to pull-only.
This skill never writes to Contentful. After generation and validation, use
`app-content-push` to update Contentful.

## When to use

Use this skill to maintain English **application-level catalog metadata**, not
to author an entire cloud marketplace listing. These scenarios map user intent
to the existing workflow; they are not separate workflows or permission to
generate every field.

| User request / example | Resolved fields or mode |
| --- | --- |
| Improve the English product introduction / "优化 Odoo 的英文产品介绍" | `summary,overview` |
| Rewrite the short tagline / "重写 Odoo 的一句话简介" | `summary` |
| Improve buyer search keywords / "改善 WordPress 的搜索关键词" | `keywords` |
| Add concrete use cases / "补充 WordPress 的使用场景" | `usecases` |
| Verify current positioning and update copy / "核实 n8n 的当前定位，更新产品介绍" | `summary,overview`, based on current official evidence |
| Curate complementary applications / "整理 n8n 的配套应用" | `relatedApps`, based on real supported relationships |
| Verify and update commercial-edition information / "核实并更新 Odoo 的商业版本信息" | `hasenterprise`, using the edition evidence rules |
| Fetch existing Contentful records / "拉取 Odoo 和 WordPress 的应用资料到本地" | Pull-only; no generated fields |

If the user names a specific field or narrower content type, use that narrower
scope rather than the broader scenario. For example, "更新 summary" does not
also authorize an overview rewrite. Resolve explicitly named products to
unambiguous catalog ids; never assume `apps=all` because no app was specified.

Do not use this skill to push reviewed content (use `app-content-push`), draft
full platform-specific listings (use `marketplace-product-content`), or inspect
marketplace listing status and versions (use `marketplace-operations-check`).
An explicit read-only review or evidence-only request must not trigger a local
refresh or generation write; report findings without changing the catalog.

## Inputs

| Input | Required | Values |
| --- | --- | --- |
| `apps` | yes | Comma-separated ids such as `odoo,wordpress`, or `all`; may be resolved from explicitly named products in the request |
| `fields` | no | Comma-separated catalog fields: `keywords`, `usecases`, `relatedApps`, `overview`, `summary`, `hasenterprise`; may be resolved from explicit natural-language generation intent |
| `refresh` | no | `true` forces a fresh pull of the target applications before generation; default `false` |
| `batch-size` | no | Applications per generation batch; default `20` |

### Intent resolution and scope constraints

Before any pull or write:

1. Resolve explicitly requested applications and intent. An explicit read-only
   instruction takes precedence over any maintenance scenario.
2. Use a supplied `fields` selection; otherwise map a clear natural-language
   generation request using the scenarios above. Combine fields only when the
   user requests both tasks for the same applications. For different per-app
   scopes, process separate selections rather than applying the union to all apps.
   Run each selection as its own batch, verify and apply.
3. A pure pull request, or a maintenance request without generation intent and
   without a field selection, resolves to pull-only. Do not treat merely naming
   a field for inspection as generation intent. If explicit parameters conflict
   with the requested task, stop and report the conflict rather than choosing
   a side or broadening the scope.
4. If generation intent is clear but its fields are not (for example "优化 Odoo
   的内容"), stop and list the required field choices. Do not default to all
   fields or silently perform a pull.
5. Generate only the apps and fields authorized by the resolved request.
   Discovering stale, empty or improvable neighboring fields does not authorize
   changing them. Cross-field/category policies apply only to selected fields;
   report other recommended changes separately. "Update the introduction" does
   not imply `refresh=true`, a rewrite of related apps or a Contentful push.
6. Report the resolved apps, mode and generation fields so the interpretation is
   traceable. Pass that same selection to generation, review and CLI commands.

If apps cannot be resolved, fields contain an unsupported name, or intent/scope
is ambiguous, print the accepted values and stop without changing anything.
This skill does not ask questions before or during generation. The completion
report may ask once about a separate Contentful push for actual validated
changes, as specified below; that offer is not authorization to write remotely.

## Reference loading

Paths here are relative to this skill directory. Commands still run from the
repository root. Before generating, read the applicable references explicitly;
loading this skill does not automatically load supporting files.

| Task / selected fields | Required reading |
| --- | --- |
| Any generation | [field-rules.md](references/field-rules.md): common formatting and selected field sections |
| `relatedApps` | [related-apps.md](references/related-apps.md): complete stack rules and store-catalog candidates |
| Framework/runtime with `usecases` or `hasenterprise` | [Frameworks and language runtimes](references/related-apps.md#frameworks-and-language-runtimes) |
| Workflow/automation app with `summary` or `overview` | [Workflow tools with AI capability](references/related-apps.md#workflow-tools-with-ai-capability), checking whether native AI applies |
| Pull-only | No generation references required |

If role classification is uncertain, inspect the applicable category sections
before writing; do not omit a cross-field exception merely because `relatedApps`
was not selected. References guide selected fields only, never expand scope.

## Preflight

### Responsibility boundary

- CLI owns pull behavior, backups, format validation, change isolation, failed
  candidate restoration and atomic local commits.
- This skill owns evidence gathering, product classification, copy generation,
  relationship decisions and semantic warning review. A CLI pass does **not**
  prove factual accuracy, edition availability, English language, lack of hype,
  correct product role or real integrations.
- Do not implement a second validator, manually restore failed applications or
  copy generated candidates over the staging catalog. Use `catalog apply`.

- Run commands from the repository root.
- `mk` is usually not on `PATH`. Use `make cli ARGS="..."`, or prefix
  `PATH="$PWD/.venv/bin:$PATH"` once.
- Read `CONTENTFUL_SPACE_ID` and `CONTENTFUL_TOKEN` from the environment. Never
  print, echo or store them. Pull accepts a Delivery/Preview token or a
  `CFPAT-` token; push requires `CFPAT-`.
- Resolve the staging path once as `<catalog>` and pass it explicitly to every
  command (`--output` for pull, `--input` for verify/apply). Keep scratch data,
  candidates, snapshots, CLI backup files and reports out of git.
- When `relatedApps` is selected, fetch the store catalog once to
  `<run>/product_en.json` from `sources.catalog` in `config/app.json`, and pass
  `--reference <run>/product_en.json` to verify/apply. Do not use the staging
  file as the authoritative candidate list; it may be partial.
- Validate `batch-size` as a positive integer and `refresh` as a Boolean before
  any write. Create a unique run directory under `/tmp/opencode` (or an approved
  `$TMPDIR`); never use shared fixed temporary filenames.

## Workflow

### 1. Choose the mode, then prepare data

Use the resolved mode and field selection above, not the mere presence or
absence of the raw `fields` parameter.

**Pull-only:** execute exactly one pull, then report and stop. For `apps=all`,
use `catalog pull --output <catalog> --allow-missing`; for specific ids use
`catalog pull --apps <ids> --output <catalog> --merge --allow-missing`.
This explicitly refreshes complete target items, including unselected local
fields. CLI creates an exact pre-write backup automatically; report its path
and that whole items were replaced.

**Generation:** load the existing catalog. If missing, pull the full catalog
once with `--output <catalog> --allow-missing`. If targets are missing, pull
only those ids with `--merge` and `--allow-missing`. With `refresh=true`, pull
the target ids once with `--apps <ids> --merge --allow-missing` (omit `--apps`
for `all`). Refresh is explicit authorization to replace entire target items,
not a field-scoped refresh; report its backup separately from generation
changes. With `refresh=false`, retain existing local content; do not silently
refresh it.

Stop on credential, network, malformed-file or unknown store-id errors. A
successful `--allow-missing` pull can omit Contentful records; report and exclude
those ids from generation, even if an old local item remains after merge.
Do not claim that an existing file was freshly verified just because it exists.

### 2. Gather evidence and snapshot

Save the post-preparation catalog as `<run>/before.json`. Track requested,
eligible, succeeded and skipped ids separately. When selecting `all`, expand
to the current local item ids and state that scope in the report.

For selected fields only, consult current official product documentation,
edition/pricing pages and integration/deployment documentation as needed.
Record URLs and uncertainties in the run report, not the catalog. Distinguish
open-source offer capabilities from commercial-only features. Role templates
and AI-development requirements in the references are editorial policies, not
evidence of bundled components or vendor functionality. The skill must review
these semantics; CLI does not classify applications or verify those policies.

### 3. Generate, review and commit in batches

Process eligible ids in catalog order, `batch-size` at a time:

1. Save the current catalog as `<run>/batch-N.before.json` and generate a full
   candidate copy `<run>/batch-N.json`, changing only requested fields. When
   `relatedApps` is selected, read candidate Trademarks from
   `<run>/product_en.json` and add `--reference <run>/product_en.json` to both
   verify and apply.
2. Perform factual and semantic review of generated fields, including evidence,
   real relationships, edition boundaries and the documented category exceptions.
   Remove insufficiently supported apps from `<batch-ids>` and record them as
   semantic skips, leaving their items unchanged in the candidate. If none
   remain, skip this batch rather than calling CLI with an empty selection.
3. Run `catalog verify --input <candidate> --apps <batch-ids> --fields <fields>
   --baseline <batch-baseline> --profile generated --json`. Inspect `issues`,
   not just exit status. Layer conflicts and hype are semantic review warnings;
   resolve them or record the justified exception. Correct errors once.
4. Commit through CLI, never by copying files:

   ```sh
   make cli ARGS="catalog apply --input <catalog> --candidate <candidate> --baseline <batch-baseline> --apps <batch-ids> --fields <fields> --skip-invalid" > <run>/batch-N.result.json
   ```

   CLI strictly validates format, rejects any isolation/structure violation,
   restores invalid selected apps in full, validates the surviving selection,
   and makes a backed-up atomic commit. It refuses a stale baseline. A non-zero
   exit is a failed commit, not permission to bypass checks or overwrite data.
5. Use the CLI result's `succeeded`, `skipped`, `changed` and `issues` as the
   mechanical checkpoint; add evidence and semantic decisions to the report.
   Resume only after inspecting persisted data and completed batch results.
   No automatic unattended resume or batch scheduling is provided by CLI.

### 4. Verify the persisted successful selection

If any apps succeeded, run:

```sh
make cli ARGS="catalog verify --input <catalog> --apps <succeeded-ids> --fields <fields> --baseline <run>/before.json --profile generated --verbose"
```

Add `--reference <run>/product_en.json` when `relatedApps` is selected.

Using succeeded ids also confirms skipped and unselected apps stayed unchanged
through generation; it does not require legacy values of skipped apps to pass
new-content checks. If none succeeded, report no generation changes. Do not
include missing or failed ids in the content-validation selection.

### 5. Report

In pull-only mode, report one row per selected application:

| app | pulled | note |
| --- | --- | --- |

For generation, use this table, one row per selected application:

| app | changed fields | validation | note |
| --- | --- | --- |

`note` carries skipped apps and reasons, unknown ids, unverified
`hasenterprise` values and any warning worth review. Framework and runtime
`false` values are policy, not unverified evidence, and need no evidence URL.

### Contentful handoff

Determine actual persisted generation changes from successful batch `changed`
results and the final comparison with `<run>/before.json`, not merely from the
requested field list. Include the catalog path and a before/after comparison for
every changed field.

Build the comparison from `<run>/before.json` (the post-refresh generation
baseline) and the final persisted catalog, after successful apps and the final
verification are known. Group it by app under a clear app id/trademark heading.
For each app with actual changes, show:

| field | before | after |
| --- | --- | --- |
| `summary` | `Previous tagline` | `New tagline` |
| `keywords` | `["old", "terms"]` | `["new", "terms"]` |
| `hasenterprise` | `false` | `true` |

Comparison rules:

- Include every actual changed field for every successful app. Do not omit or
  silently truncate values needed for the decision. Preserve complete text for
  `overview` and `summary`; render arrays as JSON so additions, removals and
  reordering are visible.
- Distinguish `<missing>` (field absent), `null`, `""` and `[]` from each other.
  Do not normalize them into an empty cell or imply that a displayed empty value
  will necessarily clear Contentful.
- Use the pre-generation baseline, not the current Contentful API state. Label
  the comparison as local catalog before/after; it does not prove Contentful is
  unchanged since the pull. The push dry run remains the authoritative remote
  diff.
- Exclude skipped/failed apps, no-op fields, unselected fields, unrelated local
  changes and pull/refresh-only differences from the proposed update set.
- Keep different per-app field scopes separate. Do not merge values from
  different apps into one comparison.
- Escape Markdown table separators and line breaks, or use code blocks, so the
  data cannot break the report layout.

For a large change set, group the comparison by app and preserve every field
above. If the result is too large for one table, write the complete comparison
to `<run>/contentful-diff.md`, include its path, and still show each app with
every changed field and the full before/after values in the report or linked
section. A shortened preview alone is not sufficient for approval.

After presenting the comparison:

- If at least one field actually changed, all proposed handoff changes passed
  mechanical validation and semantic review, and the final persisted-selection
  verification passed, present the comparison and end the report with a
  follow-up in the user's language:

  > 已更新本地目录中的 <apps / changed-fields>，尚未推送到 Contentful。
  > 变更对比见上表。
  > 是否使用 app-content-push 推送这些变更？该技能会先展示远端 before/after，
  > 再由你选择仅更新草稿、更新并发布，或取消。

  English equivalent: "Local catalog fields changed; not pushed to Contentful.
  See the before/after comparison above. Would you like to use app-content-push?
  It shows the remote before/after, then lets you update drafts, update and
  publish, or cancel."
- Offer only successful apps and their actual changed fields, keeping different
  per-app field scopes separate.
- If nothing changed, this was pull-only/read-only, or final validation failed,
  report that outcome and "Not pushed" without an unnecessary push question.
- Asking is a completion-report handoff, not an interactive execution step.
  Do not load or execute `app-content-push`, write Contentful, use `--clear` or
  publish automatically. A new explicit user approval/request authorizes the
  separate push workflow, which starts with a dry run and asks once whether to
  update drafts, publish, or cancel.
- Flag intentional empty-array changes such as `relatedApps=[]`: push skips
  empties by default. Clearing Contentful needs separate explicit authorization;
  do not imply that an ordinary push will apply a clear.

## Stop without writing

- `catalog.json` is missing or malformed and cannot be pulled.
- A requested field other than `overview` or `summary` is absent from an item.
- Evidence is insufficient for an application: skip that application, report
  why, and continue with the rest.
- A value still violates the rules after one corrective pass: drop that
  application, report it, and continue.
- Credentials are missing when a pull is required.
- The store catalog cannot be fetched when `relatedApps` is selected.
