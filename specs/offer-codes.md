# Offer codes dataset

`data/offer-codes/*.json` is the repository source of truth for per-platform
marketplace product codes. `mk catalog pull` and `mk versions` read it, so a
malformed entry silently changes those outputs.

This page covers maintenance only. How consumers use the labels is out of scope.

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

## Listing check

`mk offer-codes listing-check` asks each cloud's seller API whether every code
still resolves and how visible it is. It checks backend existence, not public
URL reachability, regional availability or purchase eligibility.

Credentials come from the environment: `AWS_*` for AWS, `ALIBABA_CLOUD_*` for
Aliyun; `alibabacloud` needs none. Azure and Huawei adapters are not implemented
and report `unsupported`.

Statuses are `exists`, `not_found`, `skipped`, `unsupported` and `error`.
`visibility` and `public` are filled when the API provides them; an existing but
non-public listing is reported separately. The command exits non-zero on adapter
errors, or on `not_found` with `--strict`.

The scheduled workflow is `.github/workflows/offer-listing-check.yml`.
