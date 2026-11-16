# Platform: azure

- Key: `azure` (Microsoft Azure Marketplace)
- Listing URL: `https://azuremarketplace.microsoft.com/en-us/marketplace/apps/{code}`
- Codes look like `publisher.offer`, for example `vmlabinc1613642184700.odoo`.
- The `azure-vmlab` platform key uses the same templates; when a platform entry
  has `overviewKey`, use the reference for that key.

## Page notes

- Title reliability is **low**. The page is a single-page app and often returns
  a generic title or HTTP 403 to automated fetches.
- One attempt only. On 403, empty shell, or generic title, mark `status`
  `unverified` and skip `versions`/`content` for that product with a note.
- Do not treat the `publisher.offer` code suffix as proof of the application
  version or edition.
- Do not report pricing, plan, or region availability from this page.

## Per-check notes

- `codes`: the code prefix is the publisher, not the application. Match the
  application using `matrix.json`, never by parsing the code.
- `status`: identity can be confirmed only from readable product content.
- `versions`: usually unreadable; expect `unverified` rather than a mismatch.
- `content`: usually unreadable; do not report missing images or descriptions
  from an empty shell.

## Do not

- Do not use the Azure Partner Center or any authenticated seller API.
- Do not infer delisting from a 403 or a blocked page.
