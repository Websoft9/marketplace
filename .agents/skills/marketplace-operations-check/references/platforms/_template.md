# Platform template

Copy this file to `references/platforms/<platform-key>.md` when adding a
platform. Do not add a platform reference without a matching key in
`config/marketplace.json`.

Required content:

1. **Key and URL** — the `platforms[].key`, `urlTemplate`, and store link.
   Note domestic vs international variants explicitly.
2. **Page notes** — title reliability, rendering model (server-rendered or
   SPA), access restrictions, and locale/language expectations.
3. **Per-check notes** — how `codes`, `status`, `versions`, and `content`
   behave on this platform, including which checks are expected to end
   `unverified` or `unsupported`.
4. **Do not** — the backend, login, or inference shortcuts that are forbidden.

Rules for every platform:

- One fetch per product URL, reused by `status`, `versions`, and `content`.
- A blocked or generic page is `unverified`, never a confirmed absence.
- Codes are resolved from `data/offer-codes/matrix.json`; never parse the
  application from a marketplace code.
- No seller backend API, no login, no retry loops.
