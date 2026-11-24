# Store product inventory

`mk store products` is the read-only, catalog-independent marketplace inventory
entry point. It returns **public image products belonging to the selected
store/provider**, including products with no corresponding application in
Contentful or the code matrix. It does not generate a matrix, resolve app
identities, check versions or modify marketplace listings.

## Commands

```sh
# Human-readable table; credentials loaded explicitly, never auto-discovered in .secrets.
mk --env-file .secrets/aliyun.env store products --platform aliyun

# JSON inventory for workflows or artifacts.
mk --env-file .secrets/aws.env store products --platform aws --json
mk --env-file .secrets/alibabacloud.env store products --platform alibabacloud --json
mk --env-file .secrets/azure.env store products --platform azure --json
mk --env-file .secrets/azure-vmlab.env store products --platform azure-vmlab --json
# Huawei China public seller search does not need credentials.
mk store products --platform huaweiyun --json
mk store products --platform huaweicloud --json
mk store products --platform googlecloud --json
# Equivalent read-only offer entry point (same options and JSON contract).
mk offer list --platform googlecloud --json

# Multiple selections are repeatable; platform order is preserved and duplicates removed.
# Configure the selected clouds' credentials in the process environment beforehand.
mk store products --platform aws --platform aliyun --json
mk store products --platform all --json
# The shared query scope is explicit; only public images are implemented.
mk store products --platform aws --visibility public --json
```

`all` means the **implemented adapters**: `aws`, `aliyun`, `alibabacloud`, `azure`, `azure-vmlab`, `huaweiyun`, `huaweicloud`, `googlecloud`, in
that order. It does not claim support for all clouds in `config/marketplace.json`.
Other platform keys are rejected before network calls. `all` cannot be combined
with another selection. `--profile NAME` selects an AWS seller profile and is
allowed only when AWS is selected; it is not passed to other adapters.

The initial scope is public **images**, not all product types or all statuses.
`--visibility public` is the default and currently the only accepted visibility.
The Python query service also validates this policy; unsupported scopes fail
before network calls, rather than silently widening a query.
There is deliberately no promise to enumerate drafts, hidden, restricted or
delisted products. Absence from the result is not proof of delisting.

## Sources and ownership

| Platform | Store/provider scope | Main product data | Visibility evidence |
| --- | --- | --- | --- |
| `aws` | Authenticated seller, Catalog `OwnershipType=SELF` (API default) | AMI `Description.ProductCode`, `ProductTitle`, `ShortDescription` | `Visibility == Public`, filtered before batch detail reads and rechecked afterwards |
| `aliyun` | 长沙网久软件有限公司, store `228399`, supplier UID `1904996544835414` | `DescribeProducts` `Code`, `Name`, `ShortDescription` | Intersection with the supplier's public store image codes and `hiddenForCloudMarket == 0` |
| `alibabacloud` | Websoft9, store `2116499` | `DescribeProducts` `Code`, `Name`, `ShortDescription` | Intersection with the publisher's public store image codes and `deliveryMethod == Image` |
| `azure` | Websoft9, publisher `websoft9inc` only | Product Ingestion VM `identity.externalId` and durable `id`; live English `listing.title/shortDescription` | Intersection with public Catalog VM offers, live generally-available submission and at least one visible public plan |
| `azure-vmlab` | VMLAB INC., publisher `vmlabinc1613642184700` only | Same Product Ingestion/live VM facts using the VMLab account | Same public Catalog/live checks scoped to the VMLab publisher |
| `huaweiyun` | 长沙网久软件有限公司, seller `b8f57c06a0f7e39f4e206560e71afd76` | Official public China seller search `id`, `title`, `hcontent` | Guest seller search with verified owner fields, `delivery_code=vm.image`, observed `content_status=1` and `hidden=0` |
| `huaweicloud` | 长沙网久软件有限公司 (display name Websoft9), seller `791bea8ce08849cf9ae4ec61c27e8435` | Official public international seller search `id`, `title`, `content_abstract` | International guest seller search with verified owner and `/intl` URLs, same observed image/visibility flags |
| `googlecloud` | Websoft9, publisher namespace `websoft9-public` | Official public console browse `productDisplayName`, `productId`, `title`, `description` | Unauthenticated `LAUNCHER`, `isPrivateCatalog=false`, selected partner facet, subtitle/namespace checks and `Virtual machines` / `PackagedDeployment` cards |

Platform supplier IDs, names and the Aliyun supplier UID are maintained in
`config/marketplace.json` under each platform's `supplier` object. The Aliyun and
Alibaba Cloud International adapters validate that metadata before querying.
Azure also validates its configured `supplier.id/name` before querying.
Both Huawei sites validate the selected platform's `supplier.id/name` before querying.

AWS `SELF` is a scope, not a seller profile ID. The command does **not** assume
that arbitrary AWS credentials belong to the configured Websoft9 seller profile.
Its store metadata therefore uses `id: "SELF"`, `provider: "Authenticated AWS
seller"`, `url: null`. Use the intended seller credentials/profile.

Aliyun validates service provider name/ID and image delivery in **both** sources.
It fetches the complete `DescribeProducts` dataset first, then enumerates the
front-end store only for ownership/image/visibility checks. Store title/summary
never override OpenAPI data. A visible image missing from the filtered API
dataset fails the query, rather than silently treating that as a complete list.

Alibaba Cloud International first enumerates Market API `2015-11-01`
`DescribeProducts` through the international endpoint (region
`ap-southeast-1`). It uses `SearchTerm=Websoft9` and `productType=MIRROR`, then
verifies `SupplierName == Websoft9`, `SupplierId == 2116499` and
`DeliveryWay == Image` locally. It then enumerates
`https://marketplace.alibabacloud.com/api/ajax/newCommodityList/queryNewList.json`
with `publisherId=2116499`, verifies `companyName == Websoft9`,
`shopUrl == /store/2116499.html` (relative or official-host URL) and
`deliveryMethod == Image`, and intersects the two sets. A visible store image
missing from the API inventory fails the query. The international endpoint
rejects the `aliyunUid` filter, so it is not used. Product URLs come from the
verified official-host store `detailUrl`; no per-product detail or version
calls are made.

`StoreVisible` is an evidence label, **not an invented OpenAPI publication
status**. Both Alibaba storefront endpoints are unversioned website interfaces;
schema changes, blocking or unverified ownership/visibility fail closed.

### Google Cloud (`googlecloud`)

`cli/googlecloud_market.py` uses the unauthenticated interface called by the
[official Websoft9 store](https://console.cloud.google.com/marketplace/browse?filter=partner:Websoft9),
verified on 2026-11-24:

```text
POST https://cloudconsole-pa.clients6.google.com/v3/entityServices/MarketplaceEntityService/entities/MARKETPLACE_BROWSE_PRODUCTS:get
```

This is an **unversioned console website interface**, not a documented seller
OpenAPI or a claim that the caller owns a producer account. The request's
`BrowseProductsRequest.parameters` fixes `locale=en_US`, `solutionScopeId=LAUNCHER`,
`isPrivateCatalog=false`, an empty search query and the configured `partner`
facet (`Websoft9`). No search-term/AI fallback or unscoped browse is accepted.
Each response must confirm that exact selected partner in
`selectedLabeledFacetValues`, return one `SEARCH_RESULT` shelf and an integer
`totalCount`. Cards must independently match `subtitle=Websoft9` and the
configured `websoft9-public/` namespace. Only `productType="Virtual machines"`
with `familyType="PackagedDeployment"` becomes image inventory; other product
types count toward complete pagination but are not emitted.

The console HTML supplies a public frontend bootstrap key (`LjgHrc`). The CLI
retrieves it afresh into memory and sends it only as `X-Goog-Api-Key` to the
fixed Google endpoint; it never hardcodes, saves, logs or exposes it in reports.
No Google account, service-account key, ADC, OAuth token, OIDC, cookies or new
SDK dependency is required or consumed. Requests disable redirects, use a
30-second timeout, and sanitize errors without raw URLs/response bodies.

`productDisplayName` is the public matrix code, for example
`websoft9-public/websoft9-r89xi`; the separate UUID `productId` remains
`entityId`. Service names, image names, license IDs and project IDs alone are
not substitutes. Public links use
`https://console.cloud.google.com/marketplace/product/{code}`. Title and short
description come directly from the card; long descriptions do not participate
in application matching. Visibility is `StoreVisible`, not an invented seller
publication status. Public discovery does not prove purchase eligibility,
enumerate drafts/private listings or prove delisting by absence.

Pagination follows opaque `nextPageToken` with observed 40-card pages; it checks
stable totals, exact page lengths, unique codes/UUIDs, token consistency and a
1000-page ceiling. Missing totals/scope evidence, recommendations instead of
search results, schema drift, malformed fields, duplicate identities or any
later-page failure invalidate the entire platform, never a successful partial
inventory. Snapshot ingestion checks the configured owner, code namespace and
UUID independently. Matrix generation shares all normal live/snapshot,
matching, dry-run, partial-match and atomic-write safeguards. Authenticated
listing edits, `offer resolve`, plan/apply and backend `listing-check` remain
unsupported for Google Cloud.

### Huawei Cloud China (`huaweiyun`)

`cli/huaweiyun_market.py` uses the public guest search endpoint called by the
[official seller page](https://marketplace.huaweicloud.com/seller/b8f57c06a0f7e39f4e206560e71afd76):

```text
POST https://mkpdata.huaweicloud.com/api/marketplace/user/api/mkp-web-guest/global/rest/mkp/v1/mkpsearchengineservice/search
```

The request follows the seller page's actual interface: `flow_id=101000`,
`filter.seller_id` from configuration, `sort=onshelfDesc`, `offset` starting at
zero and `limit=10`. It requires `error_code == "00000000"` and collects all
`pagination.items` pages through `total`, including non-image products so that
the total is checked before local image filtering. Every row must match the
configured `seller_id`, `corporation_name`, `corporation_url=/seller/<seller>`
and `url=/contents/<id>`. The public content UUID `id` is both `code` and
`entityId`. `OFFI...` offering IDs, SKU codes and IMS image IDs are not substitutes.

Only `delivery_code=vm.image` with strict integer `hidden=0` and
`content_status=1` is returned. These are **observed public storefront flags**,
not an invented official OpenAPI status enumeration; unknown states fail closed.
Explicitly hidden images and non-image products are excluded. Plain `title` and
the storefront short description `hcontent` supply the facts. HTML highlighting
is removed and entities decoded from `hcontent`; no app or version is inferred
by the store adapter. Results use `visibility=StoreVisible`, not an invented
authenticated publication status.

This source was verified against the seller page's published JavaScript on
2026-11-23. It is an unversioned website interface, with no assumed SDK/OpenAPI
stability guarantee. This first adapter is **public-store-only**: it does not
claim authenticated account ownership, enumerate drafts or prove a missing
product is delisted. No authenticated seller-list API was verified for this
implementation. AK/SK, security tokens, OIDC credentials and cookies are neither
required nor sent. No unrelated IAM/IMS API is called merely to consume credentials.

The collection fails on malformed/missing flags, wrong owner, bad code/URL,
duplicate identities, truncated or oversized pages, changing totals, network
failures, or more than 1000 pages. Redirects are disabled and errors are sanitized;
earlier pages are not emitted as complete after any later-page failure.
Snapshots additionally require UUID codes with matching `entityId` and the
configured seller metadata. Generation replaces only `productID.huaweiyun`,
preserving international and other-platform mappings.

### Huawei Cloud International (`huaweicloud`)

The same module `cli/huaweiyun_market.py` takes `platform=huaweicloud` without
mutating global state. Its source was separately verified using the
[international seller page](https://marketplace.huaweicloud.com/intl/seller/791bea8ce08849cf9ae4ec61c27e8435)
and the page's published JavaScript on 2026-11-23:

```text
POST https://searchrec.marketplace-intl.huaweicloud.com/rest/mkp/v1/mkpsearchengineservice/search
```

The international request uses `flow_id=101002`, the configured international
`filter.seller_id`, `offset`, `limit=10`, and `x-language: zh-cn`, matching the
actual page request; the flow supplies English product text. Unlike China,
the page sends no explicit sort. Both sites' stable totals, full page lengths,
identity uniqueness and pagination limits are checked identically.

International owner checks require `seller_id=791bea8ce08849cf9ae4ec61c27e8435`,
`corporation_name=长沙网久软件有限公司`,
`corporation_url=/intl/seller/<seller>`, and `url=/intl/contents/<UUID>`.
The display name `Websoft9` is not substituted for the legal `corporation_name`.
The site must return `title` and **`content_abstract`**, not China's `hcontent`;
no summary fallback is made across sites. The plain international abstract is
retained verbatim, while China continues stripping search-highlighting HTML.
Public `vm.image` / `hidden=0` / `content_status=1` filtering and UUID code rules
are identical to China's. Unknown/missing evidence or a wrong-site response
blocks a complete result; there is no fallback to China's host or store.

Store and snapshot results keep `platform=huaweicloud`, the international
supplier and source evidence. International generation replaces only
`productID.huaweicloud`, preserving `productID.huaweiyun` and every other cloud.
Both platforms can be queried concurrently without credentials. Account,
AK/SK, security tokens, OIDC, login cookies and authenticated seller access are
not needed. The public website-interface limitations above apply to both sites.

### Azure (Websoft9 and VMLab)

`cli/azure_market.py` uses Product Ingestion API `$version=2026-11-01` at
`https://graph.microsoft.com/rp/product-ingestion`. It enumerates all seller
pages with `type=azureVirtualMachine`, following `@nextLink`. Product identities
are account-scoped, not proof of the configured publisher on their own.

Publisher and public visibility are independently checked using the public
storefront Catalog interface `https://catalogapi.azure.com/offers`,
`api-version=2018-08-01-beta`, `$filter=publisherId eq '<selected publisher>'`, following
`nextPageLink`. It verifies `publisherId`, `publisherDisplayName`,
`id == publisherId + '.' + offerId`, `offerType == VirtualMachine` and explicit
boolean false values for `isPreview`, `isPrivate`, `isStopSell` and
`isExcludedFromSearch`. This older website interface was live-verified on
2026-11-22; it is not the newer API-key-protected `/products` REST API and is
not assumed to have that API's schema or support guarantees. Missing flags,
schema changes or access errors fail closed; there is no fallback to draft facts.

Every visible VM must exist in the complete authenticated seller inventory.
Only these public candidates need live `resource-tree/product/<id>` requests
(`targetType=live`), with up to four concurrent reads. Each tree must have the
requested root, target, identical VM identity and one live submission with
`status=completed`, `result=succeeded`, `lifecycleState=generallyAvailable`.
Deprecated submissions are excluded, as are products with no active visible
public plan (`audience=public`, `visibility=visible`). Unknown statuses or
missing plan evidence fail the inventory. Plan lifecycle absence uses the
documented `generallyAvailable` default; explicit deprecated/deleted plans
are not public candidates. The unique `en-us`, `kind=azureVM` listing supplies
title/summary; draft aliases and public catalog wording never override it.

The public code is `<selected publisher>.<identity.externalId>`; `entityId` retains the
separate `product/<UUID>` durable ID. Contacts, private audiences, image URLs,
tokens and raw resource trees are never emitted or saved. Requests do not follow
redirects; pagination must stay on the same HTTPS host/path and retain the query
scope. Repeated/malformed pages, duplicate identities and any failed detail read
block a complete inventory. No additional SDK or Catalog API key is needed for
this implementation. Credentials are documented in [cloud-connector.md](cloud-connector.md).

Azure requests use a **60-second per-attempt** socket timeout and at most **three
attempts total** per request (initial request plus two retries). Timeouts,
connection interruptions, temporary DNS lookup failures, incomplete response
reads and HTTP `408`, `429`, `500`, `502`, `503`, `504` are retried. Authentication
token acquisition uses the same transient-only retry policy; no product write
or publication requests are involved. A successful page/detail is not fetched
again just because another request retried.

Backoff starts at 2 then 4 seconds plus up to one second of jitter. `Retry-After`
seconds and HTTP dates are honored within a 60-second delay ceiling; malformed,
negative or oversized-invalid header values do not create an unbounded wait.
Permission/authentication failures (`400`, `401`, `403`), missing resources,
redirects, TLS/certificate failures, invalid JSON and unknown schemas are not
retried. Exhaustion still fails the entire selected inventory and prevents its
matrix write; it never silently drops the failing product.

Progress identifies the selected account (`azure`/`azure-vmlab`), authentication,
seller/catalog page, or live-detail product code, plus the retry attempt/delay.
Live-detail completion counts are also emitted. Errors and retry logs omit raw
request URLs, continuation tokens, credentials and response bodies.

`azure` selects `websoft9inc` / `Websoft9`; `azure-vmlab` selects
`vmlabinc1613642184700` / `VMLAB INC.`. Both read their own supplier metadata
from `config/marketplace.json` and retain the selected platform in snapshots.
Snapshots for one account cannot be used as facts for the other account.
The shared adapter takes publisher scope as a parameter; it never mutates global
supplier values or swaps process credentials during concurrent queries.

In the process environment, `AZURE_*` belongs exclusively to Websoft9 and
`AZURE_VMLAB_*` exclusively to VMLab. A missing or partial VMLab set fails rather
than falling back to Websoft9 credentials. The CLI maps generic `AZURE_*` keys
in a file named `azure-vmlab.env` into `AZURE_VMLAB_*` before loading it, without
overwriting the default account. For arbitrary filenames, use the namespaced
keys explicitly. See [cloud-connector.md](cloud-connector.md) for local and
simultaneous-account usage. Matrix generation for either account uses the shared
`productID.azure` column, not a new `productID.azure-vmlab` column.

AWS, Aliyun and Alibaba Cloud International use the existing optional
dependencies and environment variables documented in
[offer-codes.md](offer-codes.md) and [cloud-connector.md](cloud-connector.md).
Alibaba Cloud International reads `ALIBABA_CLOUD_*` (separate from `ALIYUN_*`)
and uses the Market SDK; the public storefront itself remains unauthenticated.
`--env-file` loads one file; multi-cloud calls require the relevant credentials
together in the environment.

## JSON contract (schemaVersion 1)

With `--json`, stdout contains exactly one JSON object. Progress and sanitized
error messages go to stderr. There are no raw SDK responses, signed requests,
credentials, aliases substituted as codes or application mappings in this output.

```json
{
  "schemaVersion": 1,
  "queriedAt": "2026-11-17T00:00:00+00:00",
  "complete": true,
  "count": 1,
  "results": [
    {
      "platform": "aliyun",
      "store": {
        "id": "228399",
        "provider": "长沙网久软件有限公司",
        "url": "https://market.aliyun.com/store/228399/index.html",
        "ownershipEvidence": "DescribeProducts SupplierId/SupplierName + storefront supplierid/shop_name"
      },
      "queriedAt": "2026-11-17T00:00:00+00:00",
      "scope": {"productType": "image", "visibility": "public"},
      "sources": [
        "Market 2015-11-01 DescribeProducts",
        "https://market.aliyun.com/api/ajax/product/queryProducts.json"
      ],
      "complete": true,
      "count": 1,
      "products": [
        {
          "platform": "aliyun",
          "storeId": "228399",
          "provider": "长沙网久软件有限公司",
          "code": "cmjj016916",
          "entityId": "cmjj016916",
          "title": "Odoo 开源企业 ERP/CRM（开发环境）",
          "shortDescription": "Example short description",
          "sellerSku": null,
          "url": "https://market.aliyun.com/detail/cmjj016916",
          "productType": "image",
          "visibility": "StoreVisible",
          "public": true,
          "visibilityEvidence": "DescribeProducts intersection with store images, hiddenForCloudMarket == 0"
        }
      ],
      "errors": [],
      "elapsedSeconds": 12.0
    }
  ]
}
```

This example illustrates the shape, not an inventory snapshot. `queriedAt` is the
UTC query start time, not the product's modification time. Product `visibility`
preserves the adapter's status/evidence label; `public: true` means it passed the
documented public-image selection. Missing public product codes or titles are
not inferred from aliases/app mappings; retained fields may be empty (for
example, a missing AWS ProductCode has a null product URL).
`sellerSku` is an optional seller-defined alias (currently AWS Sku), not a public
product code. New queries emit a string or null. Older schemaVersion 1 snapshots
without this additive field remain usable by code/entity ID, but cannot resolve
an omitted SKU alias.

`complete` means the adapter completely enumerated **the declared scope** with
valid pagination. It is not a guarantee of transactional consistency across
changing remote services or a statement about nonpublic seller inventory.

## Completeness, concurrency and failures

- Multiple platforms are queried concurrently, with independent SDK sessions and
  clients. Each platform still follows its own pagination order. Result order is
  the requested order, not completion order.
- Store pagination verifies page number, page size/content, stable totals,
  duplicate codes and page limits. AWS verifies continuation tokens, entity
  summaries and all batch detail results. No incomplete platform list is exposed
  as complete.
- A successful empty store has `complete: true`, `count: 0`, `products: []`.
  Matrix generation retains its separate empty-inventory write protection.
- Failure on one platform does not suppress successful platform lists. The failed
  result has `complete: false`, `count: 0`, `products: []` and an `errors` array
  containing `code` and a sanitized `message`. No partial platform rows survive.
- Top-level `complete` is true only if all selected platforms succeeded.
  Top-level `count` is the sum of successful product counts. Exit code is **0**
  only for a complete query, **1** for any platform failure, and **2** for invalid
  CLI arguments. Workflows must inspect both exit code and `complete`; do not
  interpret a failed platform's empty products as "store has no products".

## Shared implementation and consumers

- `cli/store_models.py`: neutral Product record and sanitized InventoryError.
- `cli/aws_inventory.py`, `cli/aliyun_market.py`,
  `cli/alibabacloud_market.py`: cloud adapters without app catalog reads.
- `cli/store.py`: shared `collect_products`, normalized JSON and independent
  multi-platform results; `get_inventory` selects live/snapshot input and
  `inventory_provenance` produces shared source metadata.
- `cli/commands/store.py`: the read-only CLI entry point.
- `mk offer-codes gen` and `mk offer resolve` use the same `get_inventory` input
  service, normalized `query_platform` result and `inventory_products` validation.
  Both explicitly select public image scope; neither bypasses the store layer
  to enumerate a seller catalog. Generation supports AWS, Aliyun China and
  Alibaba Cloud International, or a saved store JSON envelope via `--inventory
  FILE`, then performs
  application matching and matrix generation. It outputs per-product inference
  separately from unmodified store facts; see [the generation report](offer-codes.md#fact-input-and-matching-report).
  No CLI subprocess or second cloud fetch is needed. The two Alibaba platforms
  share the matching/report/matrix implementation while retaining separate fact
  adapters, store identities and matrix keys (`aliyun` and `alibabacloud`).
- `offer-update.yml` collects a fresh public store JSON snapshot, then invokes
  `offer resolve --inventory FILE --visibility public`. It includes the snapshot
  in the reviewed run artifacts; resolution does not enumerate the store again.
  The resolve report records store, source, timestamp, scope and completeness.
- Resolve remains AWS-only. Even an explicit code/SKU must be present in the
  accepted public facts; overriding application identity cannot override
  visibility. Nonpublic maintenance is not implicitly enabled.
- Snapshot facts are not a substitute for live revision-bound plan/apply reads.
  Those exact-entity reads, source/plan hashes and approval checks remain intact.
  Legacy exact-code lookup/backend-status auditing and the version-enriched
  offer-list script are not migrated by this change; they have separate purposes
  and must not be silently restricted to public inventory.
