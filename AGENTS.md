# Agent Guide

- Start with [`specs/README.md`](specs/README.md) and read the relevant spec before marketplace/data work; domain workflows live in [`.agents/skills/`](.agents/skills/).
- `mk` is the business CLI (`cli/`); development and test shortcuts are Make targets. Use `make help`, `make install`, then `make cli` (or `make cli ARGS="..."`). Create local cloud env files with `make cloud-configure cloud=<cloud>`; keep secrets in `.secrets/` and never commit them.
- Run focused tests with `make test-main`, `make test-ci`, or `make test-offers`; `make test` runs all three. Python support starts at 3.10; CI uses 3.12.
- For dashboard work in `site/`, use `npm ci`, `npm run check`, and `npm run build` from that directory; deployment builds with Node 24. Use `make site` (or `cd site && npm start`) for a foreground dev server; it automatically selects the next free port when 9001 is busy.
- Keep reusable workflows in `.agents/skills/<name>/SKILL.md` (register them in its `README.md`) and reusable automation in root `ci/`.
- Treat marketplace/platform JSON and other business data as inputs to CLI/workflows: inspect consumers and preserve schemas; avoid bulk reformatting.
- For marketplace facts, verify current platform/API sources; do not infer versions, status, or field meanings. Before opening an issue, search for an existing equivalent and include evidence for confirmed differences only.
- Keep credentials, tokens, signed requests, and sensitive API responses out of files, logs, issues, and replies. After edits, run the smallest relevant validation and state its scope/limitations.
