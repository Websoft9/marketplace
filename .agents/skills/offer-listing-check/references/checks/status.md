# Check: status

Public page reachability and identity check. One fetch per product URL, reused
by `versions` and `content`.

## Fetch method

- Plain HTTP GET, follow redirects, browser-like `User-Agent`, one attempt,
  30-second timeout.
- `python3 ci/scripts/marketplace_ops_page.py --url <url> --json` performs the
  fetch and extraction with the same rules. It never retries.
- Record the final URL. A canonical redirect is not a defect; for example a
  SKU URL may redirect to a `prodview-...` URL.
- Some marketplaces render content client-side. A 200 response with only
  navigation and a generic shell is `unverified`, not a reachable product.

## Steps

1. Fetch the listing URL once.
2. Capture: HTTP status, final URL, page title, meta description presence, and
   `blocked_hint` from the helper script.
3. Compare the title with the expected application. Strip site suffixes such as
   `| AWS Marketplace`, `- 云市场-阿里云`, and `- Alibaba Cloud Marketplace`.
   Accept Chinese product names containing the application name.

## Status values

| Status | Evidence |
| --- | --- |
| `confirmed` (reachable, identity match) | Product title names the expected application |
| `confirmed` (identity mismatch) | Title and page content clearly name another application |
| `unverified` | 403, captcha, login wall, timeout, generic title, empty shell |
| `unverified` | 404 or soft-404; not proof of delisting |
| `unsupported` | No URL template or no code for this platform |

Treat a generic title such as `AWS Marketplace`, `Aliyun`, `Alibaba Cloud`, or
`Microsoft Azure Marketplace` as no evidence either way.

## Do not

- Do not retry a blocked URL, rotate user agents, or log in.
- Do not report a product as "delisted" from page access alone.
- Do not infer payment or visibility status from the public page; that requires
  a seller backend and is out of scope.
- Do not fetch additional store or seller pages for this check.
