# Check: codes

Resolve offer codes for selected applications, report local conflicts, and add
page evidence for conflicting ownership. The local part needs no network
access; ownership evidence uses one fetch per conflicting code.

## Run the local pass

```sh
python3 ci/scripts/marketplace_ops_codes.py \
  --app odoo --app wordpress \
  --platform aws --platform aliyun --platform azure --json
```

The output contains:

| Key | Use |
| --- | --- |
| `rows` | One row per `platform + app + code`, with `variant` and `url` |
| `missing` | Selected app has no code on that platform (`informational`) |
| `multi_code` | App has several codes on one platform |
| `format_drift` | Code does not match the platform's usual format (`unverified`) |
| `conflicts` | Same `(platform, code)` owned by two apps |
| `unsupported` | Unknown apps or platforms without a `urlTemplate` |

## Ownership evidence

Run this only for `conflicts` with `in_scope: true`, and only when earlier page
checks did not already fetch that URL.

1. Fetch each owner code's listing once (`marketplace_ops_page.py`).
2. Read the title, strip site suffixes, and compare it with each owner's
   application id and clear product words.
3. Conclude:

| Evidence | Conclusion | Status |
| --- | --- | --- |
| Title clearly names one owner | That owner is the likely owner; the other assignment is suspect | `confirmed` |
| Title names neither or both owners | Manual review; list both candidates | `informational` |
| Shared bundle listing is plausible | Uncertain; do not force a removal | `informational` |
| Page unreadable or generic title | Cannot decide | `unverified` |

4. For `multi_code` apps, compare titles: same product or clear variants
   (version, plan, bundle) is normal; a title naming a different product is
   `confirmed`. More than one code is not itself a problem.

## Verified examples

- `aws / n3ukq73j0l8b3i9tjnuxrk90` is owned by `dolibarr` and `openhands` in
  the matrix, but the listing title is "OpenHands on AWS …". Conclusion:
  likely OpenHands; the Dolibarr assignment is suspect (`confirmed`).
- `alibabacloud / sgcmjj00026490` (kafka, mariadb) returned a generic
  "Alibaba Cloud" title → `unverified`, not a conflict decision.
- The OpenHands page also mentions "Dolibarr 19.0.2" in related-product
  metadata, which is why ownership evidence must use the title, not any
  version text on the page.

## Limits of this version

- Orphan-id checks against the full product catalog are not implemented. The
  staging catalog may be partial, so absence is never a delisting conclusion.
- No seller backend or listing write check.
- The run budget stays at 10 page URLs, so a full-matrix conflict sweep is out
  of scope. Report conflicts that were not investigated.

## Do not

- Do not verify whether a code still exists; that is `status`.
- Do not edit `matrix.json` or anything under `data/offer-codes/`.
- Do not treat an absent code as evidence of delisting or of a missing product.
- Do not merge two codes into one row, even when they look like the same plan.
