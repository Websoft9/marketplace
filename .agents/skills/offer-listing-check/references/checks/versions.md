# Check: versions

Compare the application version stated on the marketplace listing with the
application version baseline. Uses the page snapshot already fetched by
`status`.

## Sources

- Marketplace versions: version text in the listing title, summary, or version
  field, extracted per `specs/marketplace-version-extraction.md`.
- Baseline: `config/app.json` `sources.versions` (`apps-verson.json`), a list of
  `{ "id": "<app>", "version": ["20.0", "19.0", "latest"] }`. A local snapshot
  is allowed.
- Comparison tool: `python3 ci/scripts/marketplace_ops_version.py`.

```sh
python3 ci/scripts/marketplace_ops_version.py --app odoo \
  --marketplace 20,19,18,17 --json
```

## Extraction rules

- Prefer an explicit list attached to the application name, for example
  `Odoo 20/19/18/17`. Pass it as `20,19,18,17`.
- Accept a single version only when it is clearly the application version.
- Never pass: `imageVersion` or base OS versions (`Ubuntu 26.04`,
  `AlmaLinux 10`), build numbers (`mcmVersion`, `offlinePriceVersion`), or
  versions that belong to related products (`RecommendedVersion: Moodle 5.3`).
- If the only version-like text is one of the excluded values, treat the
  marketplace version as missing and report `unverified`.

## Comparison rules

The tool normalizes `v` prefixes and trailing zero segments, ignores the
`latest` channel, and compares numeric segments semantically.

| Situation | Status |
| --- | --- |
| Newest marketplace version equals or exceeds newest numeric baseline | `current` → `informational` |
| Newest marketplace version is lower than newest numeric baseline | `outdated` → `confirmed` |
| No marketplace version extracted | `unverified` |
| Marketplace or baseline version scheme is non-numeric | `unverified` / `unsupported` |
| Baseline has only `latest` | `unsupported` |

A prerelease is never newer than the matching stable release.

## Multiple codes per application

When an application has several codes on one platform, compare them as a set:
if any code offers the newest baseline version, the application is `current` on
that platform and each older code is `informational`, not `confirmed`
outdated. Report per-code detail anyway.

## Verified examples

- `odoo` on AWS stated `Odoo 20/19/18/17`; baseline is
  `20.0, 19.0, 18.0, 17.0, latest` → `current`.
- `wordpress` on Aliyun showed only `imageVersion: V7-Ubuntu26.04` → no
  application version → `unverified`.

## Do not

- Do not compare versions as strings, or treat `20.10` as lower than `20.9`.
- Do not guess a version from a marketing label such as "latest".
- Do not treat a missing version field as evidence of an old product.
- Do not check upstream project releases; the baseline file is the only source.
