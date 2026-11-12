# Related applications and category policies

Read this file for `relatedApps`. Read its framework/runtime section when those
roles have selected `usecases` or `hasenterprise`, and its native-AI workflow
section when those roles have selected `summary` or `overview`. If the role is
uncertain, read the applicable category sections before generation. These rules
never authorize changes to unselected fields. Shared field formatting and
evidence rules live in [field-rules.md](field-rules.md).

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
