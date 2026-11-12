---
name: offer-code-verify
description: Validate and find local marketplace offer codes, investigate listing titles, and propose scoped maintenance commands. Advisory only; never edits offer data. Read-only seller API checks require explicit backend opt-in.
---

# Offer Code Verify

## Scope

Check the local offer-codes dataset, then use the public marketplace listing
pages to explain the risky entries. `mk offer-codes verify` already reports
structure and cross-entry problems; this skill adds evidence for the two cases
a machine cannot judge from the JSON alone:

1. a code assigned to more than one application;
2. one application carrying several codes on the same platform.

This skill is **read-only**. It never edits `data/offer-codes/`, never pushes to
Contentful, and never calls a marketplace write API. Read-only seller APIs may
be queried only when the caller explicitly enables `backend=true`; available
credentials alone are not authorization to run backend checks. It ends with
recommendations for a human to apply, never executing `add`, `remove` or `replace`.

## Inputs

| Input | Required | Values |
| --- | --- | --- |
| `apps` | no | Comma-separated application ids such as `django,odoo`. Omit, or use `all`, to check every entry in the offer source |
| `platforms` | no | Comma-separated platform keys: `aws`, `aliyun`, `alibabacloud`, `huaweicloud`, `huaweiyun`, `azure`. Default: every platform present |
| `fetch` | no | `true` (default) to open listing pages; `false` for an offline pass using CLI evidence only |
| `backend` | no | `false` (default). `true` explicitly authorizes read-only seller API checks, restricted to the selected apps, codes and platforms. Cannot be combined with `fetch=false` |
| `product_catalog` | no | Full `product_en.json` URL or local snapshot for application-existence checks. For an offline pass, only a local snapshot is allowed |

If `apps` contains an id that is not in the offer source, report it as
unsupported and continue with the remaining ids. This skill does not ask
interactive questions.

## Preflight

- Run commands from the repository root.
- `mk` is usually not on `PATH`. Use `make cli ARGS="..."` or
  `.venv/bin/mk`.
- Read `data/offer-codes/` (default) or the source named by
  `MK_OFFER_SOURCE`. Do not edit it.
- Read `config/marketplace.json` for `platforms[].urlTemplate`.
- Read `data/.work/catalog.json` for `id` and `trademark` when matching a
  listing title to an application. A missing catalog is not fatal; skip
  trademark matching and say so.
- That staging catalog may contain only selected applications. It is not proof
  that an absent application is missing or delisted. Only a full product catalog
  can support a catalog-difference finding, never a delisting conclusion.
- Never print credentials.

## Workflow

### 1. Run the mechanical check first

```sh
make cli ARGS="offer-codes verify --json"
```

When `product_catalog` is supplied, pass `--product-catalog` with that source.
For `fetch=false`, never fetch a catalog URL: use the supplied local full
snapshot, or add `--skip-catalog-check` and explicitly report that catalog
membership was not checked. Reject `fetch=false, backend=true` as contradictory
inputs before running any checks. Do not infer missing applications when
`productCatalog` is null in the verification result.

- Exit non-zero with **errors**: stop. Report the exact errors; structure is
  broken and page checks are not meaningful yet.
- Warnings only (exit 0): continue. The current dataset normally has duplicate
  codes, orphan ids and length drift as warnings.
- Keep the JSON: it is the authoritative issue list. Use it for severities and
  messages. Use `mk offer-codes find --json` (with the same offer source) to
  obtain app/code/platform/label/source rows and avoid reimplementing parsing.
- Do not run backend checks yet: select the scope first.

### 2. Select the scope

- `apps` given: match entry `id` or `applications` aliases, as `find` does. Duplicate-code issues
  count as selected when at least one owner is selected.
- `apps` omitted or `all`: every entry.
- Apply `platforms` to every check.
- Record the selection in the report.

If and only if `backend=true`, run `mk offer-codes listing-check --json` with
the same `--offer` source, selected `--app` values, `--platform` values and
relevant `--code` values. Omit an app/platform filter only when the caller
actually selected all of that dimension. Always use `--limit 40` per platform;
report selected codes omitted by that limit as unverified. Never broaden a
single-app request to the entire source. Missing credentials yield `skipped`;
do not seek or provision credentials. Treat `unsupported`, `skipped` and `error`
as unverified, not missing. This check is not proof of public URL reachability
or of which application owns a listing.

### 3. Investigate duplicate codes

Build `platform -> code -> [app ids]` from the offer source. A code used by two
applications in the same platform is a conflict; the same value in two
different platforms is not.

For each conflict that touches the selection:

1. Build the listing URL from the platform `urlTemplate` in
   `config/marketplace.json`, substituting the code.
2. Open the page once and read the listing title. Strip the site suffix
   (`| AWS Marketplace`, `- 云市场-阿里云`, and similar).
3. Compare the title with each owner's `id` and `trademark` from the catalog,
   ignoring case and punctuation, and with obvious product words in the title.
4. Conclude with one of the confidence levels below.

| Evidence | Conclusion |
| --- | --- |
| Title names one owner clearly | The code belongs to that owner; recommend removing it from the other |
| Title names neither owner, or both | Ambiguous; recommend manual review and list both candidates |
| One owner is absent from the catalog | Catalog difference only; mark for review, never recommend clearing the code on this evidence alone |
| An independent current source confirms an old owner was delisted and the listing now names the other owner | Recommend moving/clearing only the confirmed old assignment, citing both sources |
| Page unreachable, blocked, or only a generic site title | `unverified`; give the URL and the reason |
| Both owners legitimately share a bundle listing | `uncertain`; explain that a shared bundle is possible, do not force a removal |

Never treat a generic page title such as `AWS Marketplace`, `Aliyun`,
`Alibaba Cloud` or `Microsoft Azure Marketplace` as a match.

### 4. List multi-code applications

For each selected application that has more than one code on one platform,
produce a row per code with the listing title and URL:

- Conclude `same family` when the titles are the same product or clear variants
  (different versions, plans or bundle tiers).
- Conclude `suspicious` when a title names a different product; recommend
  removing that code.
- Mark `unverified` when the title cannot be read.

Multi-code is legitimate for several versions or plans. Do not recommend
removal only because a list has more than one entry.

### 5. Add the remaining checks

- Orphan ids: an `id` not present in `product_en.json` (the source of truth for
  existing applications). Absence does not imply delisting: the two datasets
  are not required to match. Recommend deleting the whole entry only with
  independent, current delisting evidence for the entire entry, considering
  aliases and all platforms. Otherwise mark for review.
- Code format or length drift: report it, with the platform's usual pattern.
- Unreachable listing: a 404 on a code that is still assigned suggests a
  delisted product or a changed URL template. Report as `unverified`.
- Platform-wide emptiness: mention it once, not per application.
- Catalog applications without any offer entry: report only when `apps=all` and
  keep it to a count plus a short list.

### 6. Budget and failure handling

- Fetch only what the selection needs: duplicate codes first, then multi-code
  applications. Never fetch every code for `apps=all`.
- Cap the run at about 40 page fetches. If more are needed, fetch the first 40,
  then say which apps and platforms were not checked.
- One attempt per URL. On 403, 404 or timeout, record `unverified` and move on;
  do not retry.
- A failed fetch never changes a CLI warning into an error.

## Platform page notes

| Platform | Template from `config/marketplace.json` | Title reliability |
| --- | --- | --- |
| `aws` | `https://aws.amazon.com/marketplace/pp?sku={code}` | High; the `<title>` is the listing title |
| `aliyun` | `https://market.aliyun.com/detail/{code}` | High; the `<title>` is the listing title |
| `azure` | `https://azuremarketplace.microsoft.com/en-us/marketplace/apps/{code}` | Low; usually HTTP 403 to automated fetches |
| `alibabacloud` | `https://marketplace.alibabacloud.com/products/201072001/{code}.html` | Low; the page is a single-page app with a generic title |
| `huaweiyun` | `https://marketplace.huaweicloud.com/contents/{code}` | Low; often HTTP 404 or blocked |
| `huaweicloud` | `https://marketplace.huaweicloud.com/intl/contents/{code}` | Low; same as `huaweiyun` |

Use the platform key stored in the offer entry. Do not re-route codes yourself;
the entry already records the correct platform.

## Report

Start with the CLI result, then add the evidence.

```markdown
## Offer code check

- Source: data/offer-codes/
- Selection: django, laravel (2 of 108 entries)
- CLI: 0 error(s), N warning(s)
- Backend: disabled / N checked, M unverified (skipped, unsupported, errors or limit)
- Pages fetched: 4 (3 titles read, 1 unverified)

### Duplicate codes

| platform | code | owner A (title) | owner B (title) | conclusion |
| --- | --- | --- | --- | --- |

### Multi-code applications

| app | platform | code | title | URL | conclusion |
| --- | --- | --- | --- | --- | --- |

### Other findings

| app | finding | recommendation |
| --- | --- | --- |

### Recommendations

1. ... (each with confidence: confirmed / likely / uncertain / unverified)
```

For confirmed maintenance recommendations, include a **proposed**, not executed,
CLI command: `remove --app ... --platform ... --code ... --dry-run` or
`replace --from ... --to ... --platform ... --code ... --dry-run`. Always specify
the exact code and platform; never propose an unbounded replacement. Include
`--offer` for non-default sources. Shared bundle ownership is not itself a
reason for removal. Do not use `--skip-catalog-check` in a recommendation to
bypass an unresolved target application.

Finish with the unverified list, with the reason for each, and:

> No files changed. Apply the recommendations separately.

## Stop without fetching

- `mk offer-codes verify` reports one or more errors.
- The offer source is missing or unreadable.
- `fetch=false`, or every target platform is one of the low-reliability
  platforms: report the CLI result and the links only.
