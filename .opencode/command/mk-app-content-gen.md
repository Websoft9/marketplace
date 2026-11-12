---
description: Pull catalog data, or regenerate selected catalog fields, then validate and report.
---

Use the `app-content-gen` skill.

Do not ask questions before or during generation. The completion report may ask
once about a separate Contentful push. Read `apps` and optional `fields` from
`$ARGUMENTS`. Stop with the accepted values when `apps` is missing.

Examples:

- `apps=odoo,wordpress` — pull-only mode
- `apps=odoo,wordpress fields=keywords,summary`
- `apps=all fields=overview,summary`
- `apps=odoo fields=relatedApps,hasenterprise refresh=true batch-size=20`

$ARGUMENTS
