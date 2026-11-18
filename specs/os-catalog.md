# OS catalog

`config/os-catalog.json` is the single source for OS family names, display names
and aliases. `cli/os_catalog.py` validates it at CLI startup; there is no fallback
to a parallel code-side list. With `MK_CONFIG_PATH`, the catalog must be beside
the selected `marketplace.json`.

Each family has:

| Field | Meaning |
| --- | --- |
| `id` | Unique lowercase, hyphenated family key |
| `display` | Canonical OS family name returned to callers; also listed in `aliases` |
| `aliases` | Non-empty, case/whitespace-insensitively unique names; longer names are checked first |
| `versionExamples` | Optional strings for reference/format validation, **not** an allowlist |

The top-level `versionPattern` validates examples only; OS version extraction is
not implemented by this catalog. Detection still returns the family only:
`Ubuntu 26.04` → `Ubuntu`, `Debian 13` → `Debian`. Offer labels such as
`multiOffers` must continue to omit OS versions. To change that behavior, implement
version extraction separately rather than treating `versionExamples` as supported
release data.

The catalog is shared by OS detection, `multiOffers` validation and application-ID
matching safeguards. Preserve the existing aliases/displays unless intentionally
changing those behaviors. Missing or invalid configuration, duplicate aliases,
bad version patterns or examples fail closed before marketplace operations.

Run the focused checks with:

```sh
python -m unittest tests.test_os_catalog
```
