# Platform: aliyun

- Key: `aliyun` (Aliyun China, domestic)
- Listing URL: `https://market.aliyun.com/detail/{code}`
- Store: `https://market.aliyun.com/store/228399/index.html`

## Page notes

- The `<title>` is normally `<product> - 云市场-阿里云`; strip that suffix
  before comparing the product name. The title and meta description are
  server-rendered and available from a plain HTTP fetch.
- The product body is script-heavy. If the fetched page contains only
  navigation and footer, mark content checks `unverified`; do not infer
  absence from that HTML.
- `imageVersion` (for example `V7-Ubuntu26.04`) is the base image or OS
  generation, **not** the application version. Only count genuine application
  version text.
- Unrelated build numbers such as `mcmVersion` or `offlinePriceVersion` are not
  application versions.
- A login prompt, captcha, or slider verification blocks the check. Mark
  `unverified` and move on; do not attempt to bypass it.
- Chinese listing text is expected. Do not flag Chinese content as a defect
  for this domestic platform.

## Per-check notes

- `codes`: the same application may have several `cmjj...` codes for different
  editions or plans. Compare each code separately.
- `status`: identity matching should accept Chinese product names and common
  Chinese suffixes; otherwise mark `unverified` rather than mismatch.
- `versions`: app version text is often absent from the raw page; `imageVersion`
  is not an app version. Expect `unverified` unless the version is stated.
- `content`: description and images are in Chinese; check presence only.

## Do not

- Do not mix `aliyun` (China) with `alibabacloud` (international). Different
  marketplaces, codes, and products.
- Do not query the Aliyun Market API here; this skill is page-based only.
