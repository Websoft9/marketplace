# Check: codes

Local-only check of the offer-code matrix. No network access.

## Inputs read

- `data/offer-codes/matrix.json`: `id`, `applications`, `productID.<platform>`,
  `variants.<platform>.<code>`.
- `config/marketplace.json`: `platforms[].key`, `urlTemplate`, `overviewKey`.

## Steps

1. Read the matrix once and build `platform -> code -> [application]`.
2. Match `apps` against entry `id` **and** `applications` aliases. An id that
   matches nothing is `unsupported`.
3. For each selected application and platform, emit one row per code with the
   variant label from `variants.<platform>.<code>` when present.
4. Build the listing URL by substituting the code into the platform
   `urlTemplate`. If the platform has no template, keep the code and report
   `unsupported` for link building.
5. Check the code against the platform's usual format. Format drift is
   `unverified`, never a confirmed defect.

| Platform | Usual code format |
| --- | --- |
| `aws` | 24-character lowercase alphanumeric SKU |
| `aliyun` | `cmjj` followed by digits |
| `alibabacloud` | `sgcmjj` followed by digits |
| `azure`, `azure-vmlab` | `publisher.offer` with a dot |
| `huaweiyun`, `huaweicloud` | UUID |
| `googlecloud` | no `urlTemplate` in config; codes may be absent |

6. Detect duplicate codes: a `(platform, code)` pair owned by two different
   applications. The same code value on two different platforms is normal.
   Report a conflict as `confirmed` only when the selection includes at least
   one owner; conflicts entirely outside the selection are counted once in the
   report, without application rows.

## Findings

| Finding | Status | Meaning |
| --- | --- | --- |
| No code on a selected platform | `informational` | The app may not be sold there; not a delisting |
| Duplicate `(platform, code)` in scope | `confirmed` | Ownership conflict; needs manual review |
| Duplicate `(platform, code)` outside scope | `informational` | Count only, no rows |
| Code format drift | `unverified` | Report the observed value; do not call the listing dead |
| No `urlTemplate` | `unsupported` | No link can be built; skip page checks |

## Output

One row per `platform + application + code`:

```text
| Platform | Application | Code | Variant | URL | Status |
```

Follow with a duplicate-code summary and the list of applications that have no
code on a selected platform.

## Do not

- Do not verify whether the code still exists; that is `status`.
- Do not edit `matrix.json` or anything under `data/offer-codes/`.
- Do not treat an absent code as evidence of delisting or of a missing product.
- Do not merge two codes into one row, even when they look like the same plan.
