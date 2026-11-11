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
`mk offer apply`), then run `mk offer-codes verify` and
`mk list --update-readme README.md`.

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
