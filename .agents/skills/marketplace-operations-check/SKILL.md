---
name: marketplace-operations-check
description: Run a read-only multi-cloud marketplace operations check for selected products (offer codes, listing status, version information, content) and report evidence-backed findings. Standalone framework with per-check and per-platform references.
---

# Marketplace Operations Check

## Scope

Check selected Websoft9 marketplace products across cloud platforms. The
`codes` check is implemented; `status`, `versions`, and `content` are defined
but still validate against live pages. Search visibility, pricing, and full
compliance checks are not implemented.

This skill is standalone: do not invoke other skills. It only produces a
report. It never edits files, marketplace products, or offer data. Issue
creation is off unless `create_issue=true`, which is reserved and not yet
implemented in this version. Seller backend APIs are out of scope.

## Inputs

Parse inputs in `key=value` form (comma-separated values for lists). All inputs
are optional individually, but the resolved scope must contain at least one
platform and at least one application or product URL.

| Input | Required | Values | Default |
| --- | --- | --- | --- |
| `platforms` | yes | Platform keys from `config/marketplace.json`, e.g. `aws,aliyun,azure` | none |
| `apps` | one of `apps`/`urls` | Application ids from `data/offer-codes/matrix.json`, e.g. `odoo,wordpress` | none |
| `urls` | one of `apps`/`urls` | Public product URLs paired with `platforms` order | none |
| `checks` | no | Subset of `codes,status,versions,content` | `codes,status` |
| `backend` | no | `false` only | `false` |
| `output` | no | `report` only | `report` |
| `create_issue` | no | `false` only in this version | `false` |

`create_issue=true` is the agreed future policy: create at most one issue, only
after confirmed findings and an open-issue dedup search. It is not implemented
yet; report it as `unsupported` and still create nothing.

Resolution rules:

- Unknown platform, application, check, or output value: report it as
  `unsupported` and continue with the remaining values. If nothing remains,
  stop.
- `backend=true` is unsupported in this version: report it and run no backend
  calls. Do not substitute public pages for a backend check.
- Keep domestic and international platforms separate (`aliyun` vs
  `alibabacloud`, `huaweiyun` vs `huaweicloud`). Never re-route a code.
- Record the resolved scope at the start of the report so the caller can see
  what was and was not checked.

## Preflight

- When editing this skill or its references, run
  `python3 ci/scripts/validate_marketplace_ops_skill.py`. It must pass before
  the change is considered valid.
- Read `config/marketplace.json` and `data/offer-codes/matrix.json` once per
  run. Do not cache them across runs.

## Procedure

1. **Resolve scope.** Validate inputs. Build the target list:
   `platform -> application -> code(s) -> listing URL`. Use the platform
   `urlTemplate` and `variant` labels from `data/offer-codes/matrix.json`.
   Applications without a code on a selected platform are reported as
   `no offer code`, not as missing products.
2. **Run local checks first.** `codes` is local-only unless a conflict needs
   ownership evidence.
3. **Run page checks with a budget.** `status`, `versions`, and `content` each
   use the same product page. Fetch each URL once with
   `python3 ci/scripts/marketplace_ops_page.py --url <url> --json` and reuse the
   result. Default cap: 10 product URLs per run. If more are needed, check the
   first 10 and list the rest as omitted.
4. **Load references on demand.** Read `references/checks/<check>.md` before
   running each check, and `references/platforms/<platform>.md` for the
   selected platform, following `overviewKey` when the platform entry defines
   one. If a platform reference is missing, follow
   `references/platforms/_template.md` and mark that platform `unsupported`.
5. **Classify findings.** Use only `confirmed`, `unverified`, `unsupported`,
   and `informational`. Never turn a fetch failure, a missing field, or a local
   data gap into a confirmed product problem. For `versions`, run
   `python3 ci/scripts/marketplace_ops_version.py` instead of comparing by eye.
6. **Report.** Fill the table below and finish with limitations.

## Check registry

| Check | Network | Output | Reference |
| --- | --- | --- | --- |
| `codes` | local; 1 fetch per conflict code | Code presence, duplicates, variants, format drift, ownership evidence | [codes](references/checks/codes.md) |
| `status` | 1 fetch/product | Reachable, identity match, access restriction | [status](references/checks/status.md) |
| `versions` | reuse page | Marketplace version vs version baseline | [versions](references/checks/versions.md) |
| `content` | reuse page | Title, description, images, version visibility | [content](references/checks/content.md) |

## Stopping conditions

- Stop the affected target only, never the whole run, on captcha, login wall,
  HTTP 403/404, timeout, or a generic site title. Mark it `unverified` with the
  reason and move on.
- Do not bypass restrictions, retry blocked pages, or seek credentials.
- A 404 or unreachable page is not proof of delisting. Delisting requires
  current platform-side evidence.
- Do not compare versions when either side is missing, ambiguous, or uses an
  incompatible scheme; mark `unverified`.
- If `config/marketplace.json`, `data/offer-codes/matrix.json`, or the version
  baseline cannot be read, report the blocker instead of guessing.

## Report

```markdown
## Marketplace operations check

- Scope: platforms=aws,aliyun,azure apps=odoo,wordpress checks=codes,status,versions,content
- Checked: 3 platforms, 6 products, 4 URLs (2 omitted by budget)
- Codes: 8 in scope, 1 duplicate conflict in scope, 2 outside scope
- Backend: disabled (unsupported in this version)

| Platform | Application / code | Check | Status | Evidence / URL | Suggested action |
| --- | --- | --- | --- | --- | --- |
```

Group repeated evidence by product where useful, but keep the platform and
product code on every row. End with limitations and the unverified/unsupported
list, then state:

> No files or marketplace products changed; no issues created.
