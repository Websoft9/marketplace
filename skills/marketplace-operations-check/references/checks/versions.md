# Check: versions

Compare the version stated on the marketplace listing with the application
version baseline. Uses the page snapshot already fetched by `status`.

## Sources

- Marketplace version: listing title or short description on the product page.
- Baseline: the version source in `config/app.json` `sources.versions`
  (`apps-verson.json`), or a local snapshot the caller supplies.
- Extraction rules: `specs/marketplace-version-extraction.md`.

## Steps

1. Extract the application version from the listing using the documented
   extraction rules. Record the exact text and where it appeared.
2. Read the baseline version for the matched application.
3. Normalize both sides: ignore a leading `v`, compare numeric segments
   semantically, and never compare as plain strings.
4. A prerelease is never newer than its matching stable release.
5. Classify:

| Status | Evidence |
| --- | --- |
| `confirmed` (outdated) | Both versions parsed, same scheme, marketplace version is lower |
| `informational` (current) | Both versions parsed and equal or newer |
| `unverified` | Missing, ambiguous, or incompatible version on either side |
| `unverified` | Page was not readable, so no version text exists |
| `unsupported` | No baseline available for the application |

## Do not

- Do not guess a version from the product title when the page only shows a
  marketing label such as "latest".
- Do not treat a missing version field as "no version"; mark `unverified`.
- Do not check upstream project releases in this version of the skill. The
  baseline file is the only comparison source.
- Do not report an outdated product when platform or OS variants could carry
  different versions; note the ambiguity instead.
