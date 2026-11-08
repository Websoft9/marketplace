---
description: Validate and push selected catalog fields to Contentful drafts.
---

Use the `app-content-push` skill.

Do not ask interactive questions. Read `apps` from `$ARGUMENTS` and stop with
the accepted values if it is missing.

Examples:

- `apps=odoo,wordpress` — default content fields only, excluding trademark
- `apps=odoo,wordpress fields=summary,keywords`
- `apps=all preview=true`
- `apps=odoo publish=true`
- `apps=odoo fields=trademark` — deliberate rename

$ARGUMENTS
