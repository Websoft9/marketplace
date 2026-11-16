# Offer codes dataset

`data/offer-codes/*.json` is the repository source of truth for per-platform
marketplace product codes. `mk catalog pull` and `mk versions` read it, so a
malformed entry silently changes those outputs.

This page covers maintenance only. How consumers use the labels is out of scope.

## Generate the matrix from cloud inventory

`data/samples/matrix-history.json` is a historical format example, not a source
for generation. `mk offer-codes gen` writes the same top-level array and entry
shape to `data/offer-codes/matrix.json`; it does not add evidence fields or
`variants` to generated entries. Mechanical validation uses the existing
`offer_codes.verify` contract, not a separate schema.

```sh
# Complete AWS inventory: replace AWS mappings, preserve other clouds.
mk offer-codes gen --platform aws --profile marketplace-seller --dry-run
mk offer-codes gen --platform aws --profile marketplace-seller

# Incremental: regenerate only these codes, moving ownership when necessary.
mk offer-codes gen --platform aws --code CODE --profile marketplace-seller

# Workflow: a single JSON result on stdout, progress/errors on stderr.
mk offer-codes gen --platform aws --json > /tmp/offer-generation.json
```

- Currently only AWS generation is implemented; other platforms fail before
  any network call or write. Credentials use the normal AWS provider chain.
- AWS codes come exclusively from `Description.ProductCode` (the public
  marketplace code, typically 24–25 lowercase letters/digits), never `Sku`.
  `Sku` is only a seller-defined alias retained for explicit code lookup and
  removal of old alias mappings; it is not a fallback when ProductCode is absent.
- Generation requests only `Public` AMI products using the server-side
  `AmiProductFilters.Visibility` filter. Restricted, Limited, Draft and unknown
  visibility products are ignored, including products that become non-public
  between listing and reading their details. This is product visibility, not
  the separate AWS `Offer` entity or its targeting.
- All list pages are collected first. Details are then fetched with
  `BatchDescribeEntities` in batches of at most 20 rather than one serial
  `DescribeEntity` per product. Matching and matrix assembly are local; progress
  goes to stderr. Default output includes a summary and an unresolved-products
  table with product code, full title and reason. `--json` emits a single JSON
  object to stdout with `checked`, `matched`, `unresolved`, `visibility`,
  `elapsedSeconds`, `output` and `written`. Each unresolved row contains `code`,
  `title`, `entityId`, `candidates` and `reason`; workflows should consume these
  fields rather than repeat application matching logic. With `--json --dry-run`,
  the diff is included in the JSON `diff` field; normal dry-run keeps the diff on
  stdout and sends the human-readable report to stderr.
  Batch-level errors or omitted details block writing an incomplete inventory.
- `--product-catalog` supplies the full `product_en.json` catalog (URL or local
  file). Title trademarks take priority; only when the title has no catalog
  match is the short description before its first `,` or `，` considered.
  Reviewed `titleTrademark` exceptions are also recognized.
- Full runs enumerate every page before replacing this platform's mappings.
  `--code` is repeatable and accepts one code per occurrence. Targeted runs
  change only those codes, preserving all other mappings.
- Products with missing codes, ambiguous/unmatched titles, duplicate cloud codes
  (including repeated codes for the same application), or ambiguous selected
  aliases are skipped. The final report lists unresolved products with
  entity IDs, titles, candidates and reasons; independently resolved products
  are still written. A successful command can therefore produce a partial matrix.
- Full runs exclude unresolved codes, including any old mappings for those codes.
  Targeted runs change only successfully resolved codes and their aliases,
  preserving unresolved and unselected old mappings. No old mapping is used to
  guess a new owner. Missing selected codes are reported, not fatal when other
  selected codes resolve successfully.
- API errors, incomplete pagination, an empty inventory, or no successfully
  resolved products still block the entire write to avoid destructive replacement.
  Partial success exits 0 and exposes unresolved rows in the result. If no
  products resolve, the command emits the report with `written: false` and exits
  non-zero. Failures before matching (such as API errors) report to stderr and
  exit non-zero without a result object; workflows must check the exit code.
- Existing `applications` and `type` values are preserved. New entries use
  `type: "app"`, `applications: [id]` and all existing platform array keys.
  Legacy `variants` are removed from the generated matrix; offer differences
  belong in `data/listing/app-special-cases.json` (`multiOffers`).
- The candidate is verified before writing. Commits use the offer-source
  directory lock, a stale-snapshot check and atomic replacement. Backups have
  a hidden `.bak` filename so directory readers never load them as data.
- Generation writes local data only. It never publishes or updates listings.

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
Surrounding whitespace in `type`, ambiguous aliases across entries, and
aliases colliding with another entry's id are errors. Sharing a product
code is a separate condition and remains a warning.
`--skip-catalog-check` performs structural checks without accessing a catalog;
its output explicitly marks application membership as unchecked.

## Finding codes

```sh
mk offer-codes find [--app APP] [--code CODE] [--platform PLATFORMS] [--json]
```

Filters are optional and combine with AND. `--app` matches an entry id or one
of its `applications` aliases. A code shared by several entries produces one row
per entry, which makes duplicate ownership visible. `--json` returns
`{"codes": [...], "count": N}` with `app`, `applications`, `type`, `platform`,
`code`, `label` and `source` for every row.
`--platform all` disables platform filtering. `find` and `listing-check` reject
structurally invalid sources instead of treating them as empty; an empty JSON
array is valid. Explicit empty filter values (such as `--code ","`) are errors.

## Adding and removing codes

```sh
mk offer-codes add --app APP --platform PLATFORM --code CODE[,CODE] \
  [--label [CODE=]LABEL] [--dry-run]
mk offer-codes remove --app APP --platform PLATFORM --code CODE[,CODE] \
  [--delete-empty] [--dry-run]
```

`add`:

- accepts either the entry id or one of its `applications` aliases; the entry id
  or that alias must exist in `product_en.json`;
- refuses to write if the catalog cannot be read. An offline operation must
  explicitly use `--skip-catalog-check`, which is recorded in the output;
- only maintains entries with `type: "app"`; other types must be edited by hand;
- requires `--platform` to be one of `aliyun`, `huaweiyun`, `alibabacloud`,
  `huaweicloud`, `azure` or `aws`;
- creates the entry when it is missing. A new entry can only be added when the
  source yields one document, because the target file is otherwise ambiguous;
- refuses a code already owned by the same application, and a code owned by a
  different application on the same platform. Use `replace` to move a code, or
  `--allow-shared` for an intentional bundle listing; `verify` still reports
  the shared ownership as a warning;
- `--label` is a single label for a single code. When adding several codes, use
  `CODE=LABEL` for each label. Codes without a label are allowed.

`remove` removes the code and its variant label. An unknown code is an error
that lists the codes the application owns; the platform array stays in place
even when it becomes empty. `--delete-empty` removes the whole entry only when
no codes remain on any platform.

Both commands verify the edited dataset before writing and refuse to write when
the edit introduces new validation errors. New warnings introduced by the edit
are printed and included as `verification.newWarnings` in `--json` output.
`--dry-run` prints a unified diff; `--json` still writes unless `--dry-run` is
also given.

## Replacing an application behind a code

When a listing is delisted but its code must keep carrying a different
application, move the code instead of editing two entries by hand:

```sh
mk offer-codes replace --from old-app --to new-app --platform aws --code CODE
```

- `--platform` is repeatable or comma-separated. Omit it to use every platform
  where `--from` has codes.
- `--code` is repeatable. Omit it to move every code on the selected platforms.
  Explicitly empty `--code` or `--platform` values fail; they never mean all.
- `--from` and `--to` accept an entry id or an `applications` alias. Only
  `type: "app"` entries can be maintained; the command reports the resolved
  entry id when an alias was used.
- The command moves each code with its `variants` label. It creates the target
  entry when it is missing and requires the target entry id or requested alias
  to exist in `product_en.json`. An unreadable catalog blocks writing unless
  `--skip-catalog-check` is explicitly supplied.
- `--from` may be absent from the catalog: a delisted application is expected.
- The command refuses to write when the edit introduces new validation errors.
  New warnings are printed and included as `verification.newWarnings` in
  `--json` output. `--json` still writes unless `--dry-run` is also given.
- `--delete-empty` removes the old entry only when no codes remain. By default
  an empty entry stays and the command reports it.
- `--dry-run` prints a unified diff and writes nothing.

Changed files are rewritten with the repository's canonical JSON formatting
(4-space indent, trailing newline), through a same-directory temporary file and
`os.replace`, preserving the original file mode. `data/offer-codes/matrix.json`
round-trips unchanged, so a move produces a minimal diff.

All three edit commands hold a directory-level `.offer-codes.lock` from reading
through writing; file and directory overrides use the same lock. They also
check source snapshots before writing to detect edits from non-cooperating
tools. The service-layer `write_documents` accepts an `expected` source snapshot;
direct callers should use `source_lock` and supply that snapshot too.
All replacements and original-file backups are staged first. A replacement
failure triggers rollback of already replaced files; if rollback itself fails,
the error identifies retained original backups for recovery. This is not a
cross-file filesystem transaction: concurrent readers may see an intermediate
state, and abrupt process termination or filesystem failure can interrupt
rollback. Snapshot checks also cannot lock out arbitrary external editors.

The command only changes repository data. Publish the new application's listing
content separately (for AWS, `mk offer preflight`, `mk offer plan` and
`mk offer apply`), then run `mk offer-codes verify`.
Use `mk list` to view application links; it only prints to standard output and
does not generate or update a README section.

## Listing check

`mk offer-codes listing-check` asks each cloud's seller API whether every code
still resolves and how visible it is. It checks backend existence, not public
URL reachability, regional availability or purchase eligibility.

AWS uses the Boto3 credential provider chain (environment, `AWS_PROFILE`, SSO,
web identity, instance/container roles, etc.). Aliyun reads `ALIBABA_CLOUD_*`;
`alibabacloud` needs none. Azure and Huawei adapters are not implemented
and report `unsupported`.

Use repeated/comma-separated `--app` and `--code`, and comma-separated
`--platform`, to restrict backend calls; filters combine with AND and are
applied before the API call. App filters also match aliases. `--limit` caps
unique codes per platform. Checking a shared code preserves every entry owner
in the result. Nonexistent selections make no backend calls.

Statuses are `exists`, `not_found`, `skipped`, `unsupported` and `error`.
`visibility` and `public` are filled when the API provides them; an existing but
non-public listing is reported separately. The command exits non-zero on adapter
errors, or on `not_found` with `--strict`.
Only explicit missing-product responses (or HTTP 404/410 for the public Alibaba
Cloud endpoint) are `not_found`. Throttling, authorization failures, generic
unsuccessful replies and unrecognized payloads are `error`, not delisting
evidence. The strict success path exits zero when all selected codes exist.

The scheduled workflow is `.github/workflows/offer-listing-check.yml`.
