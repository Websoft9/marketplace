# Offer codes dataset

`mk offer-codes gen` stages a per-platform marketplace product matrix under
`data/.work/offer-codes/` by default. This directory is ignored by Git and is
not the consumer-facing source. Consumers such as `mk catalog pull`, `mk
versions`, `offer-list.yml`, and the marketplace site read the published JSON
artifact configured by `sources.offer-codes` in `config/app.json`. The current
configured artifact is `https://artifact.websoft9.com/cloud_data/offer.json`.
The matrix workflow currently uploads a GitHub Actions artifact; publishing the
generated matrix to the configured URL is a separate workflow responsibility.

This page covers maintenance only. How consumers use the labels is out of scope.

## Generate the matrix from cloud inventory

`data/samples/matrix-history.json` is a historical format example, not a source
for generation. `mk offer-codes gen` writes the same top-level array and entry
shape to its local output; it does not add evidence fields or
`variants` to generated entries. Mechanical validation uses the existing
`offer_codes.verify` contract, not a separate schema.

```sh
# Complete AWS inventory: replace AWS mappings, preserve other clouds.
mk offer-codes gen --platform aws --profile marketplace-seller --dry-run
mk offer-codes gen --platform aws --profile marketplace-seller

# Incremental: regenerate only these codes, moving ownership when necessary.
mk offer-codes gen --platform aws --code CODE --profile marketplace-seller

# Workflow: a single JSON result on stdout, progress/errors on stderr.
mk offer-codes gen --platform aws --json > /tmp/opencode/offer-generation.json

# Aliyun China: replace only Aliyun mappings, preserving AWS and other clouds.
mk --env-file .secrets/aliyun.env offer-codes gen --platform aliyun --dry-run --json
mk --env-file .secrets/aliyun.env offer-codes gen --platform aliyun --json
# Incremental: one code per --code, repeat as needed.
mk --env-file .secrets/aliyun.env offer-codes gen --platform aliyun --code cmjj016916 --json

# Collect facts once, then match/review without more marketplace requests.
mk --env-file .secrets/aliyun.env store products --platform aliyun --json > inventory.json
mk offer-codes gen --platform aliyun --inventory inventory.json --dry-run --json
# A local full catalog also avoids the catalog HTTP request (fully offline matching).
mk offer-codes gen --platform aliyun --inventory inventory.json \
  --product-catalog /path/to/product_en.json --dry-run --json

# Alibaba Cloud International: identical generation chain and report, no credentials required.
mk offer-codes gen --platform alibabacloud --dry-run --json
mk store products --platform alibabacloud --json > international-inventory.json
mk offer-codes gen --platform alibabacloud --inventory international-inventory.json --dry-run --json
# Omit --dry-run only after reviewing the diff to write productID.alibabacloud.
```

- AWS (`aws`), Aliyun China (`aliyun`) and Alibaba Cloud International
  (`alibabacloud`) generation are implemented; other platforms fail before any
  network call or write. The generator and CLI share one supported-platform
  registry and one matching/report/write implementation. AWS credentials use
  the normal AWS provider chain. `--profile` applies only to AWS and is rejected
  for both Alibaba platforms.
- AWS codes come exclusively from `Description.ProductCode` (the public
  marketplace code, typically 24–25 lowercase letters/digits), never `Sku`.
  `Sku` is only a seller-defined alias retained for explicit code lookup and
  removal of old alias mappings; it is not a fallback when ProductCode is absent.
- Generation requests only `Public` AMI products using the server-side
  `AmiProductFilters.Visibility` filter. Restricted, Limited, Draft and unknown
  visibility products are ignored, including products that become non-public
  between listing and reading their details. This is product visibility, not
  the separate AWS `Offer` entity or its targeting.
- All list pages are collected first. Details are then fetched with
  `BatchDescribeEntities` in batches of at most 20 rather than one serial
  `DescribeEntity` per product. Matching and matrix assembly are local; progress
  goes to stderr. Default output includes a summary, a per-product matching table
  (including successes), and an unresolved-products table with product code,
  full title and reason. `--json` emits a single JSON
  object to stdout with `checked`, `matched`, `unresolved`, `visibility`,
  `elapsedSeconds`, `output` and `written`. Each unresolved row contains `code`,
  `title`, `entityId`, `candidates` and `reason`; workflows should consume these
  fields rather than repeat application matching logic. Full per-product
  decisions are in `products` as documented below. With `--json --dry-run`,
  the diff is included in the JSON `diff` field; normal dry-run keeps the diff on
  stdout and sends the human-readable report to stderr.
  Batch-level errors or omitted details block writing an incomplete inventory.
- `--product-catalog` supplies the full `product_en.json` catalog (URL or local
  file). The [shared v2 identity rules](application-matching.md) prefer each app's
  full normalized trademark, then Apache-prefix fallback and eligible app ID.
  The earliest app occurrence wins; exact priority ties remain ambiguous. Only
  when the title has no hit is the summary before its first `,` or `，` considered.
  Reviewed `titleTrademark` exceptions remain recognized.
- Full runs enumerate every page before replacing this platform's mappings.
  `--code` is repeatable and accepts one code per occurrence. Targeted runs
  change only those codes, preserving all other mappings.
- Products with missing codes, ambiguous/unmatched titles, duplicate cloud codes
  (including repeated codes for the same application), or ambiguous selected
  aliases are skipped. The final report lists unresolved products with
  entity IDs, titles, candidates and reasons; independently resolved products
  are still written. A successful command can therefore produce a partial matrix.
- Full runs exclude unresolved codes, including any old mappings for those codes.
  Targeted runs change only successfully resolved codes and their aliases,
  preserving unresolved and unselected old mappings. No old mapping is used to
  guess a new owner. Missing selected codes are reported, not fatal when other
  selected codes resolve successfully.
- API errors, incomplete pagination, an empty inventory, or no successfully
  resolved products still block the entire write to avoid destructive replacement.
  Partial success exits 0 and exposes unresolved rows in the result. If no
  products resolve, the command emits the report with `written: false` and exits
  non-zero. A failed/incomplete live inventory emits a report with `written:
  false`, inventory errors and no product decisions. Catalog failure emits
  `unverified` product matches and blocks the write. Preflight errors (invalid
  arguments, malformed existing matrix or invalid inventory snapshot) can exit
  without a JSON result; workflows must always check the exit code.
- Existing `applications` and `type` values are preserved. New entries use
  `type: "app"`, `applications: [id]` and all existing platform array keys.
  Legacy `variants` are removed from the generated matrix; offer differences
  belong in `data/listing/app-special-cases.json` (`multiOffers`).
- The candidate is verified before writing. Commits use the offer-source
  directory lock, a stale-snapshot check and atomic replacement. Backups have
  a hidden `.bak` filename so directory readers never load them as data.
- Generation writes local data only. It never publishes or updates listings.
- Live inventory goes through `store.query_platform`, the normalized facts query
  behind [`mk store products`](store-products.md), rather than invoking a CLI
  subprocess or separately collecting products. Store queries preserve all public
  image products regardless of application mapping; `offer-codes gen` then adds
  identity matching and local matrix generation. It does not duplicate cloud
  enumeration or read products back from the matrix.
  `store.get_inventory` and `inventory_provenance` provide the shared live/snapshot
  selection and metadata used by generation and `offer resolve`; generation
  always requests `visibility="public"` explicitly.

### Fact input and matching report

There are three distinct stages: **store facts → application matching → matrix
decision**. The store query never performs application matching. Generation first
obtains its facts, then loads the application catalog and runs identity rules.

Without `--inventory`, generation calls the same live platform query used by
`mk store products`. With `--inventory FILE`, it reads the **schemaVersion 1
JSON envelope** emitted by that command. No marketplace requests or credentials
are needed for snapshot mode. `--profile` and `--inventory` cannot be combined.
The catalog still uses its configured URL unless `--product-catalog` is local.

The snapshot must contain exactly one result for the selected platform. That
result must be complete, error-free, have a timezone-aware query timestamp,
matching supported store/provider ownership, public image scope, consistent
counts and well-formed public product records. Restricted/nonpublic facts,
duplicate entity IDs and wrong-platform/store rows are rejected. A failure of
an **unselected** platform in a multi-cloud snapshot does not block a complete
selected platform. Missing code/title text is not invented; normal unresolved
handling still applies to retained empty fields.

Snapshots are explicitly supplied local input, not cryptographically attested
or automatically refreshed. Their collection time remains visible; do not treat
an old snapshot as proof of today's listing state. Full regeneration from a
snapshot can replace the selected cloud's mappings just like a live run; review
its age and the dry-run diff before writing.

The generation JSON keeps existing counters and `unresolved` rows, adding:

| Field | Meaning |
| --- | --- |
| `schemaVersion` | Generation report contract version, currently 1; this is not a store snapshot |
| `inventory` | Selected platform's store, scope, sources, query time, count, completeness/errors; `source` is `live` or `snapshot`, with `sourcePath` for snapshots |
| `matching.catalog` | Application catalog source; URL userinfo, query and fragment are removed to avoid exposing credentials/signed parameters |
| `matching.rulesVersion` | Identity rule revision (`deterministic-first-appearance-v2`) |
| `matching.catalogIdentitySha256` | SHA-256 of effective application identity names, including reviewed branding exceptions; not a hash of the raw catalog file |
| `matching.matchedAt` | UTC matching-stage start time, or null if facts could not be accepted |
| `matching.complete`, `matching.errors` | Whether identity evaluation was possible; complete does **not** mean all products matched |
| `matching.degraded`, `matching.warnings` | Record-level missing trademark diagnostics and reviewed-name/safe-ID fallback; warnings are also printed to stderr |
| `products` | Each selected actual product's known store facts, plus `appMatch` and `matrixDecision` |
| `errors` | Pre-matching fact validation/empty-inventory failures when a report can be emitted |

`appMatch` is inference, never an alteration of the store facts:

```json
{
  "appMatch": {
    "status": "matched",
    "appId": "odoo",
    "candidates": [],
    "method": "title-trademark"
  },
  "matrixDecision": {
    "included": true,
    "reason": null
  }
}
```

- `status`: `matched` (one selected owner), `ambiguous` (exact tied owners), `unmatched` (none), or
  `unverified` (catalog/rules could not be loaded). No ownership is guessed from
  old matrix assignments, seller SKUs or URLs.
- Missing catalog trademarks can degrade to reviewed branding or an eligible
  application ID, with explicit warnings and without dropping the record.
  Missing names without a safe fallback, malformed data or duplicate IDs still
  block matrix writes; diagnostic errors identify this separately from a normal
  product `unmatched` decision.
- `appId`: the unique matched ID, otherwise null. `candidates` lists IDs only
  for ambiguity, avoiding duplication of a confirmed `appId`.
- `method`: `title-` or `summary-lead-` plus `trademark`, `apache-short-name` or
  `app-id`; null without evidence. Titles remain authoritative. `selection` is
  `first-appearance`; `evidence` records competing app hits, normalized positions,
  matched names and selection flags. See [the full matching specification](application-matching.md).
- `matrixDecision.included`: whether the product code is eligible for this
  candidate matrix, **not** whether a write happened. `reason` explains rejection.
  A uniquely matched application can still be excluded for missing/duplicate
  codes or ambiguous explicit SKU selection. Always check the decision, not just
  `appMatch.status`.

`matched` retains its existing meaning: **included unique codes**, not necessarily
the number of `appMatch.status == matched` rows. `written` alone records whether
the local matrix was actually committed. `--dry-run` leaves it false even if
codes are eligible. The matrix file schema itself is unchanged.

For `--code`, `products` contains the selected actual products only; inventory
metadata still describes the full source scope. Requested codes missing from
the facts remain in `unresolved` but do not get fabricated product rows.
Store facts are not mutated by matching. In particular, `sellerSku` is only an
optional lookup/cleanup alias, never a replacement for `code` or identity evidence.

### Aliyun China inventory

- Install the existing optional dependency with `pip install '.[aliyun]'`.
  Credentials are `ALIYUN_ACCESS_KEY_ID` and `ALIYUN_ACCESS_KEY_SECRET`, with
  optional `ALIYUN_SECURITY_TOKEN` for STS. The command uses the same client
  setup as `mk versions`; SDK errors are sanitized to avoid leaking signed
  requests.
- Scope is the image products of **长沙网久软件有限公司**, public store `228399`,
  supplier UID `1904996544835414`. The UID is public store metadata, not a
  credential. This is Aliyun China only, not Alibaba Cloud International.
- The adapter first enumerates Market API `2015-11-01` `DescribeProducts`, using
  the documented `Filter` keys `productType=MIRROR` and
  `aliyunUid=1904996544835414`. The SDK method is `set_Filters`. Every page is
  checked against `PageNumber`, `PageSize` and `TotalCount`; changing totals,
  repeated/missing product codes, malformed pages and API errors block writing.
- It then enumerates the public store's
  `https://market.aliyun.com/api/ajax/product/queryProducts.json` using
  `supplierId`, `pageIndex`, `pageSize`. It requires consistent `curPage`,
  `totalPage`, `totalRecords` and complete page contents, and verifies
  `shop_name`, `supplierid`, `delivery_method == "镜像"` and
  `hiddenForCloudMarket == 0`. Missing/unknown visibility fails closed.
- Only API rows also present in the visible store image set are selected.
  `SupplierName`, `SupplierId` and `DeliveryWay` are verified again. A visible
  store image missing from the filtered API inventory blocks writing, rather
  than silently clearing a possibly valid mapping. Hidden, foreign-supplier
  and non-image products are not candidates for application matching.
- Product codes are `ProductItem.Code`, titles are `Name` and summaries are
  `ShortDescription`. The list response supplies all three, so generation
  never needs an individual `DescribeProduct` call. Both sources are enumerated
  completely before local matching and matrix assembly.
- `DescribeProducts` exposes no publication/visibility status fields; do not
  invent a published-status enum. The report uses `visibility: "StoreVisible"`
  to describe the actual evidence (API + public storefront intersection), not
  an AWS-style `Public` status. This storefront endpoint is not a versioned
  OpenAPI contract; schema changes or blocking stop generation without writes.
- The same partial-success, dry-run, table/JSON output, validation, atomic-write
  and backup rules apply. A full Aliyun run replaces only `productID.aliyun`;
  other clouds' mappings and existing application metadata are preserved.

API contract: [DescribeProducts documentation](https://next.api.aliyun.com/document/Market/2015-11-01/DescribeProducts)
and [official machine-readable metadata](https://api.aliyun.com/meta/v1/products/Market/versions/2015-11-01/apis/DescribeProducts/api.json).

### Alibaba Cloud International inventory

- Uses the same **store facts → application matching → matrix decision →
  preview/atomic write** chain as Aliyun China. `--inventory`, `--code`,
  `--product-catalog`, `--dry-run`, JSON/human reports, provenance, partial success
  and all write protections are shared; there is no international-specific
  matcher, candidate builder or writer.
- Only fact collection differs: international products come from the public
  Websoft9 store `2116499`, through the existing
  `alibabacloud_market.list_products` adapter behind `store.query_platform`.
  It validates supplier/store ownership, Image delivery and complete pagination.
  See [store-products.md](store-products.md) for the source contract.
- This path does **not** call the China `DescribeProducts` API or read Aliyun
  credentials. The public store supplies code, title, short description and URL
  without a credential or per-product detail request. As with the China
  storefront evidence, report visibility is `StoreVisible`, not an invented
  OpenAPI publication status.
- China and international inventories are not interchangeable. `cmjj...` codes
  remain under `productID.aliyun`; `sgcmjj...` codes remain under
  `productID.alibabacloud`, with no prefix stripping or cross-platform inference.
  A full international run replaces only `productID.alibabacloud`, preserving
  China, AWS and all other platform mappings and application metadata. A
  China-only snapshot is rejected for an international generation request, and
  vice versa.

## Entry shape

```json
{
  "id": "haproxy",
  "productID": {
    "aws": ["43of0ubgxuf0k88if0noeafl8", "e16iyvd40gcn4zsak80ijzkbz"]
  },
  "type": "app",
  "applications": ["haproxy"],
  "variants": {
    "aws": {
      "43of0ubgxuf0k88if0noeafl8": "Ubuntu",
      "e16iyvd40gcn4zsak80ijzkbz": "Debian"
    }
  }
}
```

`variants` is optional: `platform -> code -> label`.

## Rules

- `variants` keys must exist in `productID`; unknown platforms or codes are
  errors.
- A label is one string. Use `;` to separate several labels, for example
  `"Ubuntu; Enterprise"`. Empty segments, leading/trailing whitespace and
  non-string values are errors.
- Labels are optional per code. For a platform with several codes, at least one
  code must carry a label when `variants` is present; covering every code is not
  required.
- A single-code entry may also carry a label.
- Record the OS family name from the marketplace listing's "Operating system"
  value, without the version number: `Ubuntu`, `Debian`, `RHEL`.

## Consistency with product_en.json

`product_en.json` is the source of truth for which applications exist.
`mk offer-codes verify` compares offer entries with it and reports differences
as **warnings**, never errors:

- an offer entry whose `id` is absent from `product_en.json`;
- a `product_en.json` application without an offer entry, reported as one
  aggregate warning.

The two files are not required to match. Use `--product-catalog` to point at a
local snapshot instead of the remote URL, and `--no-check-coverage` to skip the
second warning. These consistency warnings do not fail `--strict`.

## Validation

```sh
mk offer-codes verify
```

The command reports structure and cross-entry problems. Run it after editing;
errors fail the check, warnings do not.
Surrounding whitespace in `type`, ambiguous aliases across entries, and
aliases colliding with another entry's id are errors. Sharing a product
code is a separate condition and remains a warning.
`--skip-catalog-check` performs structural checks without accessing a catalog;
its output explicitly marks application membership as unchecked.

## Finding codes

```sh
mk offer-codes find [--app APP] [--code CODE] [--platform PLATFORMS] [--json]
```

Filters are optional and combine with AND. `--app` matches an entry id or one
of its `applications` aliases. A code shared by several entries produces one row
per entry, which makes duplicate ownership visible. `--json` returns
`{"codes": [...], "count": N}` with `app`, `applications`, `type`, `platform`,
`code`, `label` and `source` for every row.
`--platform all` disables platform filtering. `find` and `listing-check` reject
structurally invalid sources instead of treating them as empty; an empty JSON
array is valid. Explicit empty filter values (such as `--code ","`) are errors.

## Adding and removing codes

```sh
mk offer-codes add --app APP --platform PLATFORM --code CODE[,CODE] \
  [--label [CODE=]LABEL] [--dry-run]
mk offer-codes remove --app APP --platform PLATFORM --code CODE[,CODE] \
  [--delete-empty] [--dry-run]
```

`add`:

- accepts either the entry id or one of its `applications` aliases; the entry id
  or that alias must exist in `product_en.json`;
- refuses to write if the catalog cannot be read. An offline operation must
  explicitly use `--skip-catalog-check`, which is recorded in the output;
- only maintains entries with `type: "app"`; other types must be edited by hand;
- requires `--platform` to be one of `aliyun`, `huaweiyun`, `alibabacloud`,
  `huaweicloud`, `azure` or `aws`;
- creates the entry when it is missing. A new entry can only be added when the
  source yields one document, because the target file is otherwise ambiguous;
- refuses a code already owned by the same application, and a code owned by a
  different application on the same platform. Use `replace` to move a code, or
  `--allow-shared` for an intentional bundle listing; `verify` still reports
  the shared ownership as a warning;
- `--label` is a single label for a single code. When adding several codes, use
  `CODE=LABEL` for each label. Codes without a label are allowed.

`remove` removes the code and its variant label. An unknown code is an error
that lists the codes the application owns; the platform array stays in place
even when it becomes empty. `--delete-empty` removes the whole entry only when
no codes remain on any platform.

Both commands verify the edited dataset before writing and refuse to write when
the edit introduces new validation errors. New warnings introduced by the edit
are printed and included as `verification.newWarnings` in `--json` output.
`--dry-run` prints a unified diff; `--json` still writes unless `--dry-run` is
also given.

## Replacing an application behind a code

When a listing is delisted but its code must keep carrying a different
application, move the code instead of editing two entries by hand:

```sh
mk offer-codes replace --from old-app --to new-app --platform aws --code CODE
```

- `--platform` is repeatable or comma-separated. Omit it to use every platform
  where `--from` has codes.
- `--code` is repeatable. Omit it to move every code on the selected platforms.
  Explicitly empty `--code` or `--platform` values fail; they never mean all.
- `--from` and `--to` accept an entry id or an `applications` alias. Only
  `type: "app"` entries can be maintained; the command reports the resolved
  entry id when an alias was used.
- The command moves each code with its `variants` label. It creates the target
  entry when it is missing and requires the target entry id or requested alias
  to exist in `product_en.json`. An unreadable catalog blocks writing unless
  `--skip-catalog-check` is explicitly supplied.
- `--from` may be absent from the catalog: a delisted application is expected.
- The command refuses to write when the edit introduces new validation errors.
  New warnings are printed and included as `verification.newWarnings` in
  `--json` output. `--json` still writes unless `--dry-run` is also given.
- `--delete-empty` removes the old entry only when no codes remain. By default
  an empty entry stays and the command reports it.
- `--dry-run` prints a unified diff and writes nothing.

Changed files are rewritten with the repository's canonical JSON formatting
(4-space indent, trailing newline), through a same-directory temporary file and
`os.replace`, preserving the original file mode. The generated matrix remains
under the ignored `data/.work/` work area and is not committed as repository data.

All three edit commands hold a directory-level `.offer-codes.lock` from reading
through writing; file and directory overrides use the same lock. They also
check source snapshots before writing to detect edits from non-cooperating
tools. The service-layer `write_documents` accepts an `expected` source snapshot;
direct callers should use `source_lock` and supply that snapshot too.
All replacements and original-file backups are staged first. A replacement
failure triggers rollback of already replaced files; if rollback itself fails,
the error identifies retained original backups for recovery. This is not a
cross-file filesystem transaction: concurrent readers may see an intermediate
state, and abrupt process termination or filesystem failure can interrupt
rollback. Snapshot checks also cannot lock out arbitrary external editors.

The edit commands only change the ignored local runtime matrix. Their changes
are not durable repository configuration and a later full generation can replace
the selected platform mappings. Publish listing content separately (for AWS,
`mk offer preflight`, `mk offer plan` and `mk offer apply`); run
`mk offer-codes verify --offer data/.work/offer-codes/matrix.json` to validate
the current local matrix.
Use `mk list` to view application links; it only prints to standard output and
does not generate or update a README section.

## Listing check

`mk offer-codes listing-check` asks each cloud's seller API whether every code
still resolves and how visible it is. It checks backend existence, not public
URL reachability, regional availability or purchase eligibility.

AWS uses the Boto3 credential provider chain (environment, `AWS_PROFILE`, SSO,
web identity, instance/container roles, etc.). Aliyun reads `ALIYUN_*`;
`alibabacloud` needs none. Azure and Huawei adapters are not implemented
and report `unsupported`.

Use repeated/comma-separated `--app` and `--code`, and comma-separated
`--platform`, to restrict backend calls; filters combine with AND and are
applied before the API call. App filters also match aliases. `--limit` caps
unique codes per platform. Checking a shared code preserves every entry owner
in the result. Nonexistent selections make no backend calls.

Statuses are `exists`, `not_found`, `skipped`, `unsupported` and `error`.
`visibility` and `public` are filled when the API provides them; an existing but
non-public listing is reported separately. The command exits non-zero on adapter
errors, or on `not_found` with `--strict`.
Only explicit missing-product responses (or HTTP 404/410 for the public Alibaba
Cloud endpoint) are `not_found`. Throttling, authorization failures, generic
unsuccessful replies and unrecognized payloads are `error`, not delisting
evidence. The strict success path exits zero when all selected codes exist.

This command is an ad hoc check against the locally generated matrix. The
scheduled listing-check workflow was removed because an ignored runtime
artifact is not a persistent expected-code baseline. A fresh matrix contains
currently public products only, so regenerating immediately before the check
cannot reveal codes that have disappeared or become non-public. To make that
kind of historical comparison reliable, a separate persistent baseline would
be required.
