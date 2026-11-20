# Application field rules

Read the common rules and the sections for selected fields before generation.
Apply rules only to the authorized field selection. See
[related-apps.md](related-apps.md) for relationship rules and category
exceptions that also affect `usecases`, `hasenterprise`, `summary` and `overview`.

This file has two tiers:

- **Hard constraints** are mechanical and enforced by
  `mk catalog verify --profile generated`. Fix every reported error. Passing
  them is not semantic approval.
- **Editorial guidance** is judgment the verifier does not check. The agent
  owns it.

## Hard constraints

Every row is an error under `--profile generated`. The verifier is
authoritative; this table is a summary.

| Field | Constraint |
| --- | --- |
| `keywords` | array, at least one item; each 1–50 characters, lowercase, unique, no surrounding or trailing punctuation |
| `usecases` | array, 1–10 items; each at most 4 words, Title Case, unique, no surrounding or trailing punctuation |
| `overview` | string, 1–350 characters, no surrounding whitespace, ends with `.` |
| `summary` | string, at most 10 words, no surrounding whitespace, no trailing punctuation, must not contain `open source` or `open-source` |
| `description` | non-blank Markdown string, at most 150 whitespace-separated source tokens in total, including any lists; source whitespace is preserved |
| `relatedApps` | array, at most 8 items; exact catalog Trademarks, no self-reference, no generic runtime, no duplicates |
| `hasenterprise` | JSON Boolean |
| generated `keywords`, `usecases`, `overview`, `summary` | ASCII punctuation, except inside the item's trademark |

`mk catalog verify` also warns without failing about hype wording, an `overview`
that repeats the summary, and several `relatedApps` from the same layer. Treat
warnings as review prompts.

## Editorial guidance

### Language and tone

- Write English (`en-US`), regardless of the conversation language.
- State what the app does, not how good it is. Avoid `leading`, `best`,
  `world-class`, `cutting-edge`, `revolutionary` and `trusted by`.
- Keep product names and trademarks as published in prose.

### `keywords`

- Let the evidence decide the count; do not force a fixed 15 and do not copy the
  current count. Typical listings use 8–15.
- Order keywords by priority: the leading entries are the ones retained when a
  platform applies a count or length cap.
- Use buyer search language: product, category, modules, workflows, intent.
- Do not pad with generic words, competitors, or claims.

Good: `["erp", "crm", "inventory", "accounting", "purchase orders", "point of sale"]`

Bad: `["best erp", "world-class software", "leading platform"]`

### `usecases`

- Concrete scenarios, not capabilities or marketing claims.
- Application frameworks and language runtimes must include `AI Coding`; see
  [Frameworks and language runtimes](related-apps.md#frameworks-and-language-runtimes).
- Avoid product names such as `n8n` here; the Title Case rule rejects their
  lowercase spelling. Use `Workflow Automation`, not
  `Workflow Automation with n8n`.

Good: `["Sales", "CRM", "Inventory", "Accounting"]`

Bad: `["Improve business efficiency with powerful tools"]`

### `relatedApps`

Read [related-apps.md](related-apps.md) before generating this field. Its stack
rules, layer exceptions and examples are editorial guidance; the mechanical
limits (count, exact Trademarks, generic runtime, self-reference) are in the
table above. When the staging catalog is partial, pass `--reference` to
`catalog verify` and `catalog apply` so the Trademark check uses the full store
catalog.

### `overview`

- One factual string in sentence case.
- What the app is and its main purpose. Do not repeat the summary, and do not
  add unverified editions, versions, support terms or certifications.
- Target 20–40 English words: add the product category, intended purpose and a
  useful capability or workflow beyond the short `summary`. This is editorial
  guidance, not a CLI-enforced minimum; do not pad a genuinely simple product.
- Keep the existing hard limit of 350 characters. If the word target conflicts
  with that limit, prefer a clear, factual overview within 350 characters.
- Use the vendor's current category. If an AI product now positions itself as an
  agent platform or agent workspace, say that instead of keeping an obsolete
  "chat" label.
- Create the field when missing or null.

Good: `Odoo is an open-source business application suite combining sales, CRM, accounting, inventory and website tools in one modular platform.`

Bad: `Odoo is the world's leading all-in-one business software trusted by millions of companies.`

### `description`

- Write English Markdown about the application itself: its distinctive
  capabilities, concrete feature highlights and the problems those features
  solve. Explain what makes it useful rather than padding a category definition
  or repeating the summary/overview.
- Include at least one substantive, connected prose paragraph, not just a
  tagline or a list. A paragraph alone is valid. Optionally combine prose with a
  short bullet list when that improves readability; lists are never mandatory.
- Target 80–120 English words to give this field a clear depth step beyond the
  overview. The target is editorial guidance, not a CLI-enforced minimum; never
  pad copy or add unsupported details to reach it.
- Keep the entire field within the existing 150-word maximum, including optional
  lists. The CLI uses
  a conservative whitespace-separated Markdown-source token count: standalone
  Markdown markers and link targets also consume budget when separated by
  whitespace. Do not pad the text or use formatting to evade the limit.
- Prioritize specific, evidence-backed application features and differentiators.
  Protocols, integrations and workflow examples are useful when officially
  supported. Do not invent capabilities, imply commercial-only features are
  included in the open-source offer, or replace app features with marketplace
  deployment instructions, Websoft9 services or generic marketing claims.
- Ecosystem maturity is optional: describe supported integrations, extensibility
  or established tooling qualitatively when supported by current official
  evidence. Do not include precise ecosystem/popularity metrics such as GitHub
  stars, contributor counts, plugin counts or user/download totals.
- `open source` and `self-hosted` are allowed when accurate; the summary's ban
  on open-source wording does not apply here. Preserve Markdown whitespace and
  do not apply overview character limits or forced final punctuation.
- Create the field when missing or null, but only when `description` is selected.
- Review completeness, paragraph structure, feature accuracy and ecosystem
  claims semantically; the CLI only checks type, non-blank content and the
  generation word limit. Store evidence URLs in the report, not as evidence
  annotations in the generated copy.

Style example based on the user's Uptime Kuma sample (not a substitute for
checking current official documentation; never copy its unfinished final
sentence into generated content):

> Uptime Kuma is an open-source, self-hosted monitoring tool for tracking the
> availability of websites and network services. Its web interface brings
> HTTP(s), TCP and ICMP checks into one place, helping users detect outages
> across different services. Notifications through email, Slack and Discord
> let teams route availability alerts to their existing communication channels
> and respond when a monitored service becomes unavailable.

### `summary`

- One sentence with the accurate defining characteristic.
- Target 5–10 English words. This is a compact identifier, not a miniature
  overview; its existing 10-word ceiling remains a hard constraint.
- Spend the word budget on the role, domain or differentiator. Every catalog app
  is open source, so that phrase adds no information.
- For a workflow tool with native AI features, include `AI` or `agent`.
- Create the field when missing or null.

Good: `Modular ERP and business apps suite`

Bad: `Open-source modular ERP and business apps suite`

Good: `AI workflow automation and integration platform`

Bad: `The best ERP for every business`

### `hasenterprise`

- `true`: a separately licensed commercial/enterprise edition exists and this
  marketplace offer provides the open-source edition. Keep or write `true`
  unless there is explicit evidence for `false`. Do not encode the edition name
  in generated copy: display labels live in
  `data/listing/app-special-cases.json` and default to `Community Edition`.
- `false` only from an official vendor editions or pricing page that lists a
  single edition, or an explicit vendor statement that no commercial edition is
  offered. Do not infer it from the app name, popularity, open-source status or
  the current value.
- Exception: application frameworks and language runtimes are always `false`
  without pricing-page evidence; see [Frameworks and language runtimes](related-apps.md#frameworks-and-language-runtimes).
- Keep an existing `false` unless new evidence contradicts it.
- When evidence is insufficient, keep `true` and report it as `unverified true`.
- Record the evidence URL in the report, never in the catalog. Do not ask for
  confirmation; the agent decides from the evidence.
