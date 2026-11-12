# Application field rules

Read the common formatting rules and the sections for selected fields before
generation. Apply rules only to the authorized field selection. CLI validation
is mechanical; evidence and semantic review remain skill responsibilities.
See [related-apps.md](related-apps.md) for relationship rules and category
exceptions that also affect `usecases`, `hasenterprise`, `summary` and `overview`.

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
  [Frameworks and language runtimes](related-apps.md#frameworks-and-language-runtimes).

Good: `["Sales", "CRM", "Inventory", "Accounting"]`

Bad: `["Improve business efficiency with powerful tools"]`

### `relatedApps`

Read [related-apps.md](related-apps.md) before generating this field. The complete
stack rules, layer exceptions and examples live there rather than being duplicated.

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
  marketplace offer provides the open-source edition. Keep or write `true`
  unless there is explicit evidence for `false`. Do not encode the edition name
  here: display labels live in `data/listing/app-special-cases.json` and default
  to `Community Edition`.
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
