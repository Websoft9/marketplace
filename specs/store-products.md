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

# Multiple selections are repeatable; platform order is preserved and duplicates removed.
# Configure the selected clouds' credentials in the process environment beforehand.
mk store products --platform aws --platform aliyun --json
mk store products --platform all --json
# The shared query scope is explicit; only public images are implemented.
mk store products --platform aws --visibility public --json
```

`all` means the **implemented adapters**: `aws`, `aliyun`, `alibabacloud`, in
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

Platform supplier IDs, names and the Aliyun supplier UID are maintained in
`config/marketplace.json` under each platform's `supplier` object. The Aliyun and
Alibaba Cloud International adapters validate that metadata before querying.

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
