---
description: Validate offer codes and check marketplace listing titles for duplicate or multi-code entries.
---

Use the `offer-code-verify` skill.

Do not ask interactive questions. Read `apps`, optional `platforms` and optional
`fetch` from `$ARGUMENTS`. With no application ids, check every entry in the
offer source.

Examples:

- `apps=django,laravel`
- `apps=all platforms=aws`
- `apps=odoo fetch=false`
- no arguments — every entry, every platform

$ARGUMENTS
