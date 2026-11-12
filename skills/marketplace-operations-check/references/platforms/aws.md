# Platform: aws

- Key: `aws`
- Listing URL: `https://aws.amazon.com/marketplace/pp?sku={code}`
- Seller profile: `https://aws.amazon.com/marketplace/seller-profile?id=c639a579-182c-4d30-8578-4d4d89fba658`

## Page notes

- The listing `<title>` normally contains the product name; treat it as high
  reliability for identity matching. Strip the `| AWS Marketplace` suffix.
- A SKU URL may redirect to a canonical `prodview-...` URL. Follow it and record
  the final URL; this is normal.
- The page is server-rendered: title, meta description, and product wording are
  available from a plain HTTP fetch.
- Related-product metadata can leak into the page, for example a
  `RecommendedVersion` for a different application. Use only version text that
  belongs to this product.
- Region and currency may default from the request location. Do not report a
  price or region difference from a single page view.
- A consent or locale interstitial is not a product finding; mark `unverified`
  if it blocks the product content after one attempt.

## Per-check notes

- `codes`: each `sku` is a separate listing; `variants.aws.<code>` labels
  describe the plan. Do not merge two SKUs into one finding without evidence.
- `status`: a 404 can also mean the SKU was replaced by a newer one. Report
  `unverified` and suggest checking the seller profile.
- `versions`: version text may appear only in the title or an AMI detail line.
- `content`: images may be lazy-loaded; scroll or wait before calling an image
  missing.
