# Deterministic application identity matching

Rule revision: **`deterministic-first-appearance-v2`**.

The identity engine in `cli/offer_resolve.py` is shared by `offer-codes gen`
and `offer resolve` (used by `offer-apply.yml`). It is deterministic string
matching, not keyword extraction, fuzzy matching or an LLM. No manually maintained
application alias table is required. Existing reviewed `titleTrademark` rendering
exceptions remain valid alternate full names.

## Normalization and boundaries

Only matching copies of names/text are normalized; cloud facts and catalog display
fields are never modified. The engine:

1. Removes display marks `™`, `®`, `℠` **before** Unicode NFKC normalization
   (otherwise NFKC turns `™` into the literal letters `TM`).
2. Applies NFKC, Unicode case-folding and whitespace collapse.
3. Normalizes common typographic dashes to `-`.
4. Preserves meaningful punctuation in names such as `C++`, `C#`, `.NET`, `Node.js`.

A name must have no adjacent ASCII letter, digit or underscore:

```regex
(?<![a-z0-9_])<escaped normalized name>(?![a-z0-9_])
```

This is not a beginning-of-title `^` requirement. Names may occur anywhere in the
field. Spaces, hyphens, brackets and Chinese text are valid boundaries;
`Moodle中文版` can match, but `supermoodle`, `moodlebox` and `my_moodle` cannot.
Attached version digits such as `Moodle5` are not silently split into a brand and
version by this identity matcher.

## Name priority and field priority

For **each application**, try these forms in order, stopping at its first
successful tier in the current text field:

1. Full normalized `trademark` or reviewed `titleTrademark`.
2. If a full name starts with `Apache `, remove **only that leading prefix** and
   try the remaining full phrase (minimum four characters). No arbitrary vendor,
   edition, word or suffix stripping is performed.
3. The normalized application ID, subject to conservative eligibility checks.

ID-derived forms must be ASCII identifier-shaped, at least four characters or
contain a digit (`n8n`), and must not be a vendor prefix of a known OS family.
Thus `php`, `go`, `r`, `ai` are not automatically introduced as aliases of longer
names, although an explicit full trademark `PHP` still works. `oracle` is not
inferred from a weak ID occurrence: the existing OS family `oracle linux` makes
that vendor-only name unsafe for the Oracle Database application. A full
`Oracle Database` match remains valid. This reuses the [shared OS vocabulary](os-catalog.md)
loaded from `config/os-catalog.json`,
not an application-specific alias/denylist.

First run these tiers against the title. **Any title hit takes precedence over
all summary hits**, including a title ID-derived hit versus a full summary brand.
Only if there are no title hits is the short description used, limited to the
text before the first English or Chinese comma. Capability mentions after that
comma remain excluded.

The full-name preference is per application, not a global instruction to let
another application's later full name suppress an earlier derived name.
`Superset with PHP` therefore selects Superset, not PHP. Within one app's selected
tier, use its earliest occurrence; at the same start, use its longer full phrase.

## Multiple applications: first appearance

After selecting the best name tier for each application, rank app hits by:

1. Earliest start offset in the normalized field.
2. At the same start: full trademark, Apache-shortened name, then ID-derived name.
3. At the same start and strength: longer matched phrase.

If different apps still have the exact same priority, retain `ambiguous`; never
break a genuine tie using catalog/dictionary order. Title ambiguity is not
overridden by the summary.

| Title | Result |
| --- | --- |
| `Nextcloud 企业网盘` (catalog trademark `Nextcloud Hub`) | `nextcloud`, ID-derived |
| `Apache Superset` | `superset`, full trademark |
| `Superset 数据可视化` | `superset`, Apache-shortened |
| `Moodle 在线学习` (catalog trademark `Moodle™`) | `moodle`, normalized full trademark |
| `Laravel-PHP` | `laravel`, because it appears first |
| `PHP for Laravel` | `php`, because it appears first |
| `PostgreSQL (pgvector)` | `postgresql`, because it appears first |
| `pgvector for PostgreSQL` | `pgvector`, because it appears first |
| `Oracle Linux with GNOME` | Not inferred as Oracle Database from ID `oracle` |
| Same full phrase assigned to two app IDs | `ambiguous` if both are tied |

First appearance is an explicit business rule, **not proof of the publisher's
intended semantic ownership**. Mentioning an integration before the actual app
can select that integration. Preview and review changes before writing mappings
or approving a cloud update; do not describe the result as semantic certainty.

## Evidence and consumer policies

`identify_details` returns the existing status/appId/candidates/method fields plus:

- `selection: "first-appearance"`.
- `evidence`: all app hits in the decisive field, with `appId`, matched normalized
  `name`, `kind`, `field`, zero-based `start`, exclusive `end` and `selected`.
  Positions are **normalized-text offsets**, not raw cloud-text offsets. Later
  matches are retained with `selected: false`. A true tie has multiple selected
  candidates but `status: ambiguous` and no accepted `appId`.

Methods combine `title-` or `summary-lead-` with `trademark`, `apache-short-name`
or `app-id`. `identify` and the compatibility `identify_evidence` call the same
engine rather than implementing alternate rules. `match_trademark` has no real
application ID argument and therefore does not introduce its internal placeholder
as an ID alias.

`offer-codes gen` exposes this object as `products[].appMatch` and keeps code
eligibility in `matrixDecision`. Duplicate/missing codes remain excluded even
when the application is identified. `matching.rulesVersion` records this revision.

`offer resolve` records the same `appMatch` and `identityRulesVersion` in resolved
targets, so Action artifacts retain the decision evidence. Distinct app matches
at different positions now resolve automatically using first appearance. Exact
identity ties, duplicate entity/code matches and multiple products for the same
offer variant still fail automatic resolution. Explicit `--code` overrides remain
explicitly marked and do not rewrite the actual identity evidence.

Application matching is separate from product visibility, offer-variant detection,
version extraction and cloud write approval. These other policies do not change.
