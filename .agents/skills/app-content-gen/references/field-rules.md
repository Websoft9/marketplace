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
| `relatedApps` | array, at most 8 items; exact catalog Trademarks, no self-reference, no generic runtime, no duplicates |
| `hasenterprise` | JSON Boolean |
| generated text | ASCII punctuation, except inside the item's trademark |

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
- Use the vendor's current category. If an AI product now positions itself as an
  agent platform or agent workspace, say that instead of keeping an obsolete
  "chat" label.
- Create the field when missing or null.

Good: `Odoo is an open-source business application suite combining sales, CRM, accounting, inventory and website tools in one modular platform.`

Bad: `Odoo is the world's leading all-in-one business software trusted by millions of companies.`

### `summary`

- One sentence with the accurate defining characteristic.
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
