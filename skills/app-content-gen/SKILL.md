---
name: app-content-gen
description: Non-interactive workflow to pull catalog data, or pull and regenerate selected fields for selected applications in data/.catalog/catalog.json, validate mechanically, and report. Does not push to Contentful.
---

# Application Content Gen

## Scope

Pull catalog data, or pull and regenerate selected English listing fields, for
selected applications in `data/.catalog/catalog.json`. In generation mode,
preserve every other application, every unselected field, `schemaVersion`,
`generatedAt`, `count`, `source` and item order. In pull-only mode, refresh the
selected items and let the pull update `generatedAt` and `count`.

When `fields` is omitted, this skill only pulls the selected applications and
stops. That is the standalone pull mode. It never writes to Contentful. After
generation and validation, use `app-content-push` to update Contentful.

## Inputs

| Input | Required | Values |
| --- | --- | --- |
| `apps` | yes | Comma-separated ids such as `odoo,wordpress`, or `all` |
| `fields` | no | Comma-separated catalog fields: `keywords`, `usecases`, `relatedApps`, `overview`, `summary`, `hasenterprise`. Omit for pull-only mode |
| `refresh` | no | `true` forces a fresh pull of the target applications before generation; default `false` |
| `batch-size` | no | Applications per generation batch; default `20` |

If `apps` is missing, or `fields` contains any name outside the list, print the
accepted values and exit without changing anything. This skill does not ask
interactive questions.

## Preflight

- Run commands from the repository root.
- `mk` is usually not on `PATH`. Use `make cli ARGS="..."`, or prefix
  `PATH="$PWD/.venv/bin:$PATH"` once.
- Read `CONTENTFUL_SPACE_ID` and `CONTENTFUL_TOKEN` from the environment. Never
  print, echo or store them. Pull accepts a Delivery/Preview token or a
  `CFPAT-` token; push requires `CFPAT-`.
- `data/.catalog/catalog.json` is git-ignored scratch data. Back it up before
  editing and never commit it.

## Workflow

### 1. Ensure current catalog data

Load `data/.catalog/catalog.json`.

- File missing: pull the whole catalog with
  `make cli ARGS="catalog pull"`.
- Any target id absent: pull those ids with
  `make cli ARGS="catalog pull --apps <missing-ids> --merge"`.
- `refresh=true`: pull the target ids with
  `make cli ARGS="catalog pull --apps <target-ids> --merge"`.

`--merge` replaces the pulled items and preserves every other item and the
existing order. Do not pull with `--apps` without `--merge` into an existing
catalog: that would replace the file with only the pulled subset.

If credentials are missing or the pull fails, stop. Do not generate from a
partial or stale file. If a target id is still absent after the pull, report it
as unsupported and continue with the remaining ids.

**Pull-only mode:** when `fields` is omitted, always pull the selected
applications and stop; do not snapshot, generate or validate content.

- `apps=all`: `make cli ARGS="catalog pull"` (full refresh).
- Specific ids: `make cli ARGS="catalog pull --apps <target-ids> --merge"`
  (refresh those items, keep every other item).

Report the pulled ids, the resulting item count, and any missing ids. Do not run
the generation, validation or push steps.

### 2. Snapshot the input

Copy the current file to `${TMPDIR:-/tmp}/catalog.before.json`. All validation
compares against this snapshot.

### 3. Generate in batches

Process the target ids in catalog order, `batch-size` at a time. For each
batch:

1. Read the selected items, classify each app's role, and collect the full list
   of catalog `trademark` values. Build the `relatedApps` stack before writing.
2. Copy the current catalog to `${TMPDIR:-/tmp}/catalog.batch.before.json`.
3. Generate only the requested fields for each item.
4. Apply the batch to a copy of the catalog, for example
   `${TMPDIR:-/tmp}/catalog.batch.json`.
5. Validate the copy (step 4) against the batch snapshot. If it passes, replace
   `catalog.json` with the copy. If it fails, correct once and re-validate; if it
   still fails, drop the failing applications from the batch, keep the passing
   ones, and report the skipped ones.

Batching limits prompt size, keeps a failed application from blocking the
others, and gives the report a natural checkpoint. To resume after an
interruption, rerun with the ids that are still missing from the report.

### 4. Validate mechanically

Run:

```sh
make cli ARGS="catalog verify --input ${TMPDIR:-/tmp}/catalog.batch.json --apps <batch-ids> --fields <fields> --baseline ${TMPDIR:-/tmp}/catalog.batch.before.json --require-non-empty --verbose"
```

The command checks the field rules below and fails if any item outside the
batch or any unselected field changed. It also warns about repeated layers in
`relatedApps` (multiple relational stores, DB admins, model runtimes, vector
stores or SCM tools). Treat those warnings as review items: fix them unless the
app is one of the documented exceptions. Fix errors once; do not suppress them.
Warnings are reported but do not block. Re-read the persisted file after the
batch is copied.

After the last batch, verify the whole selection against the original snapshot:

```sh
make cli ARGS="catalog verify --apps <target-ids> --fields <fields> --baseline ${TMPDIR:-/tmp}/catalog.before.json --require-non-empty --verbose"
```

This confirms that no application outside the selection and no unselected field
changed across the whole run.

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
Finish with: "Not pushed. Use the app-content-push skill to update Contentful."

## Field rules

### Output language and formatting

- Write English (`en-US`), regardless of the conversation language.
- Use ASCII punctuation; keep product names and trademarks as published.
- `keywords`: lowercase, no surrounding or trailing punctuation.
- `usecases`: Title Case, no trailing punctuation.
- `overview`: sentence case, ends with `.`, no surrounding whitespace.
- `summary`: sentence case, no trailing `.`, no surrounding whitespace.
- Avoid hype: `leading`, `best`, `world-class`, `cutting-edge`, `revolutionary`,
  `trusted by`. State what the app does, not how good it is.

### `keywords`

- Unique strings, at least one, each at most 50 characters.
- Let the evidence decide the count; do not force a fixed 15 and do not copy
  the current count. Typical listings use 8–15.
- Use buyer search language: product, category, modules, workflows, intent.
- Do not pad with generic words, competitors, or claims.

Good: `["erp", "crm", "inventory", "accounting", "purchase orders", "point of sale"]`

Bad: `["best erp", "world-class software", "leading platform"]`

### `usecases`

- 1–10 unique phrases, at most 4 words each.
- Concrete scenarios, no surrounding or trailing punctuation.
- Application frameworks and language runtimes must include `AI Coding`; see
  Frameworks and language runtimes.

Good: `["Sales", "CRM", "Inventory", "Accounting"]`

Bad: `["Improve business efficiency with powerful tools"]`

### `relatedApps`

Build a **working stack**, not a list of similar apps. One app per layer, only
real relationships.

- Array of 0–8 unique **Trademarks of applications that already exist in
  `catalog.json`**. Use the exact `trademark`, never the `id`.
- Read the candidate set from `catalog.json` at runtime. Do not rely on a
  hardcoded app list; offerings change.
- Include only real relationships: the image bundles, preinstalls or requires
  the app, or the app is a complement that forms a working solution.
- Exclude the target itself, alternatives/competitors, and generic runtime
  (`Docker`, `Nginx`, `Linux`, `Kubernetes`).
- Order: required data store → category-critical capability → auxiliary.
- Leave the array empty when no real relationship exists.

#### One app per layer

Do not list two apps that fill the same layer, even when the target supports
both. This is the most common failure mode.

| Layer | Pick at most one | Notes |
| --- | --- | --- |
| Data store | PostgreSQL, MySQL, MariaDB, MongoDB, SQL Server Express, Oracle Database, ClickHouse, Elasticsearch, OpenSearch | Never list several RDS alternatives |
| DB admin | pgAdmin, phpMyAdmin, CloudBeaver, RedisInsight | Prefer the native admin for the chosen DB |
| Cache / queue | Redis, RabbitMQ, Kafka | Only when required by the app |
| Model runtime | Ollama, LocalAI | One runtime, not both |
| Vector store | Chroma, Weaviate, Milvus | One vector store, not several |
| AI UI / workspace | Open WebUI, LobeHub, AnythingLLM | Pick the one that complements, not competes with, the target |
| AI app platform / orchestration | Dify, n8n | One platform per stack |
| Automation | n8n, Apache Airflow, Activepieces, Inngest | One orchestrator |
| SCM | GitLab, Gitea | One source-control system |
| CI | Jenkins, TeamCity, GoCD | One CI server |
| Quality / security | SonarQube, Trivy | One quality layer |
| Artifact registry | JFrog Artifactory, Docker registry | Container vs generic artifact registries may both be valid |
| Testing | Selenium | One test runner |
| Collection | Prometheus, Elastic Logstash, Kafka | One collector per pipeline |
| Storage | Elasticsearch, OpenSearch, ClickHouse, InfluxDB | One store per pipeline |
| Analysis / BI | Grafana, Elastic Kibana, Metabase, SuperSet | One visualization layer |

If the app already bundles a layer, do not add an alternative for that layer.
For example, a DevOps platform with built-in CI and registry should not list a
second CI server or a second registry; add only external complements.

Exception: a universal database client, BI platform or visualization tool
exists to connect to many sources. List at most three representative data
sources, not every supported database, and do not add a second admin client.

Exception: application development frameworks and language runtimes list the
common project data stores by design, not as competing alternatives; see
Frameworks and language runtimes below.

Exception: a data pipeline may legitimately span one ingest layer, one storage
layer and one warehouse/streaming layer (for example a message broker, a
PostgreSQL metadata store and ClickHouse). That is different from listing
several interchangeable RDS options.

#### Pick one data store

When the app supports several databases, choose one primary store:

- Use the vendor's documented default or recommendation.
- Otherwise: PostgreSQL for modern self-hosted platforms, MySQL for LAMP/PHP
  apps, MariaDB for MariaDB-native apps, SQL Server Express for .NET apps.
- Add one admin for that store: `pgAdmin` for PostgreSQL, `phpMyAdmin` for
  MySQL/MariaDB, `CloudBeaver` when the app is a universal DB client.
- Do not list PostgreSQL, MySQL and MariaDB together. They are alternatives,
  not a stack.
- If the target is itself a database, list its admin/client, not another
  database. Do not add a second admin client either.

#### Frameworks and language runtimes

Application development frameworks and language runtimes are development
platforms, not finished business apps: for example Django, Laravel, Spring Boot,
Next.js, and PHP, Python or Node.js runtime images.

- `hasenterprise`: always `false`. The framework or runtime itself is offered as
  a single free edition. Commercial add-ons from the same vendor — hosted
  services, support subscriptions or separately sold products — are not a
  commercial edition of this marketplace offer. This is the only category where
  `false` needs no editions or pricing page evidence.
- `relatedApps`: a framework is expected to connect to several stores, so
  describe the typical project stack instead of choosing one store, in this
  order:
  1. `MySQL` and `PostgreSQL`;
  2. `MongoDB` when the catalog sells it;
  3. `Redis` for cache and sessions;
  4. one universal admin tool, `CloudBeaver`.
  Add `MariaDB` only when the vendor documents it as the default. Cap the list
  at 8, use exact catalog trademarks, and drop any name the catalog does not
  sell.
- `usecases`: must include `AI Coding`. Prefer a second AI-development scenario
  so that two entries relate to AI development, for example
  `AI Code Generation`, `AI Agent Development` or `LLM App Development`. Keep
  the usual 1–10 items and four-words-per-item limit.
- Do not list another framework or runtime, the target itself, or generic
  runtime as a relationship.
- This subsection overrides "pick one data store" and the one-app-per-layer rule
  for relational data stores. The validator warning about multiple relational
  stores is expected for this category and is not a defect.

#### Role templates

| Role | Cover at least |
| --- | --- |
| Enterprise / business app | primary DB + its admin; cache/queue only if required |
| Framework / language runtime | data stores (MySQL, PostgreSQL, MongoDB) + Redis + universal admin; `usecases` include `AI Coding` |
| Website / CMS | DB + DB admin + cache/load + access analytics |
| DevOps / SCM / CI | SCM + CI + quality + registry + testing, as applicable; DB/cache only as required infrastructure |
| AI / LLM platform | model runtime + vector store + UI/workspace + automation; one per layer |
| AI-enabled workflow | workflow engine + local model runtime + DB/cache; include the runtime when the tool has native AI steps or agent nodes |
| Data platform | ingest/collect + store + analyze/BI |
| Monitoring / analytics | collection + storage + visualization |
| Messaging / infra | console + proxy/load balancing + observability |
| Home / media / IoT | automation + media + DB |

Examples of the failure mode (trademarks must still be re-read from the current
catalog):

- `Gitea: [PostgreSQL, MySQL, MariaDB]` → `[PostgreSQL, Redis, Jenkins, SonarQube]`
- `Umami: [PostgreSQL, MySQL]` → `[PostgreSQL, pgAdmin]`
- `LobeHub: [Ollama, LocalAI]` → `[PostgreSQL, Ollama]`
- `Jenkins: [GitLab, Gitea, ...]` → keep one SCM, then CI/registry/quality/testing

Do not stop at database entries for a DevOps tool, and do not list several
databases for an analytics or AI tool. If a layer is not required and no real
complement exists, omit it.

#### Workflow tools with AI capability

If a workflow or automation tool has native AI features — agent nodes, LLM
steps, AI connectors or AI workflow templates — treat it as AI + workflow:

- mention AI or agents in `summary` and `overview`;
- include the catalog's local model runtime (usually `Ollama`) in
  `relatedApps`, plus the existing DB/cache;
- keep one runtime only; `LocalAI` is an alternative, not an addition.

Examples at the time of writing: `n8n`, `Activepieces`, `Inngest`. Do not add AI
wording to pure data orchestrators or IoT flow editors without native AI
features; for those, mention ML pipelines only when the product actually
supports them.

Good: `n8n` → `["PostgreSQL", "Redis", "Ollama"]` and summary
`AI workflow automation and integration platform`.

Bad: `n8n` → `["PostgreSQL", "Redis"]` with summary
`Visual workflow automation platform` (misses native AI capability).

If a list already has more than 8 values because a human added them, do not
truncate it. If the user asks to regenerate `relatedApps`, the new AI-generated
list is capped at 8.

Database backup/HA is usually built-in (replication, PITR, snapshots) and is not
a `relatedApps` entry. List HA only when a bundled catalog app provides it. The
catalog currently has no backup app; keep backup as a built-in feature and do
not invent one.

### `overview`

- One factual string, at most 350 characters including spaces and punctuation.
- What the app is and its main purpose. Do not repeat the summary, and do not
  add unverified editions, versions, support terms or certifications.
- Use the vendor's current category. If an AI product now positions itself as an
  agent platform or agent workspace, say that instead of keeping an obsolete
  "chat" label.
- Create the field when missing or null.

Good: `Odoo is an open-source business application suite combining sales, CRM, accounting, inventory and website tools in one modular platform.`

Bad: `Odoo is the world's leading all-in-one business software trusted by millions of companies.`

### `summary`

- One sentence, at most 10 whitespace-separated words.
- The accurate defining characteristic, no hype or unverified superlatives.
- Never use `open source` or `open-source` in a summary. Nearly every catalog
  app is open source; the phrase wastes the 10-word budget. Spend the words on
  the role, domain or differentiator instead.
- For a workflow tool with native AI features, include `AI` or `agent` in the
  summary.
- Create the field when missing or null.

Good: `Modular ERP and business apps suite`

Bad: `Open-source modular ERP and business apps suite`

Good: `AI workflow automation and integration platform`

Bad: `The best ERP for every business`

### `hasenterprise`

- JSON Boolean only: `true` or `false`, never a string.
- `true`: a separately licensed commercial/enterprise edition exists and this
  marketplace offer is the Community Edition. Keep or write `true` unless
  there is explicit evidence for `false`.
- `false` only from an official vendor editions or pricing page that lists a
  single edition, or an explicit vendor statement that no commercial edition is
  offered. Do not infer it from the app name, popularity, open-source status or
  the current value.
- Exception: application frameworks and language runtimes are always `false`
  without pricing-page evidence; see Frameworks and language runtimes above.
- Keep an existing `false` unless new evidence contradicts it.
- When evidence is insufficient, keep `true` and report it as `unverified true`.
- Record the evidence URL in the report, never in the catalog. Do not ask for
  confirmation; the agent decides from the evidence.

## Stop without writing

- `catalog.json` is missing or malformed and cannot be pulled.
- A requested field other than `overview` or `summary` is absent from an item.
- Evidence is insufficient for an application: skip that application, report
  why, and continue with the rest.
- A value still violates the rules after one corrective pass: drop that
  application, report it, and continue.
- Credentials are missing when a pull is required.
