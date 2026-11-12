# Check: content

Basic content presence check on the page snapshot already fetched by `status`.
This is not a compliance or quality review.

## Fields from the helper script

`ci/scripts/marketplace_ops_page.py` returns `title`, `description`,
`og_image`, `image_count`, `version_samples`, and `blocked_hint`. Use these
values, then confirm against the visible page when the result is ambiguous.

## Steps

1. If `blocked_hint` is true or the page is a shell, mark the product
   `unverified` and skip content judging.
2. Record presence, not quality, for:

| Item | Counts as present when | Does not count |
| --- | --- | --- |
| Title | Page title names the product | Generic site title |
| Summary / description | Meta description or a visible summary paragraph | Empty meta tag |
| Description body | Real product detail beyond the summary | Navigation or footer text |
| Images | At least one product image or screenshot | Site logo, icon, or nav sprite only |
| Version information | The application version is stated | Only `imageVersion` / base OS, or unrelated build numbers |
| Plan / edition / price | A plan, edition, or price indicator is shown | A generic "buy now" button |

3. Mark each item `present`, `missing`, or `unclear`.

## Status values

| Status | Evidence |
| --- | --- |
| `confirmed` (gap) | Item is clearly absent from a fully rendered page |
| `informational` (ok) | All checked items are present |
| `unverified` | Page incomplete, blocked, or item state ambiguous |
| `unsupported` | Platform page does not expose this field publicly |

A gap is `confirmed` only when the image count, title, and description show the
page did render. A page with a valid title but no description is `unverified`
until a second signal (visible body text or structured data) confirms the
absence. This check never performs that second fetch; report `unverified`.

## Do not

- Do not judge wording quality, brand compliance, or legal compliance here.
- Do not mark an item missing from a lazy-loaded or partially rendered page.
- Do not compare against other marketplaces' listings.
- Do not download images, videos, cases, or qualification documents.
