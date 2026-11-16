# Issue-driven offer updates (paused)

The former product-file Issue workflow is **paused** after moving application
facts out of the repository.

Historical status:

- Application facts now come temporarily from
  `data/.work/catalog.json`; the repository no longer stores
  `data/listing/products/`.
- Repository files own templates, vendor statements and AWS listing rules.
- The old workflow edited product JSON in a PR and is incompatible with this
  source model. Its workflow, Issue form and implementation were removed.
- A future Issue workflow must write application facts to Contentful, not to a
  repository product file.

Offline AWS offer work currently uses:

```sh
mk offer build --app odoo --cloud aws
mk offer plan --app odoo --profile marketplace-seller --output /tmp/odoo-plan.json
```

`build` writes `data/.work/aws/odoo.json`. `plan` remains live,
revision-bound and is the only step that prepares a submit-ready ChangeSet
request. Publication requires required source fields, a resolved Catalog
EntityId and an explicitly reviewed plan.

## Manual GitHub Actions apply workflow

`.github/workflows/offer-apply.yml` runs manually for one
application and one cloud. Inputs are the catalog application id, an optional
display name used for the run label, a cloud selector (only platforms
implemented by the workflow are listed), an optional `code`, and an optional
`offer` variant. The variant is a value from the application's `multiOffers` in
`data/listing/app-special-cases.json`; `base` means the offer with no extra
label. It does not publish files committed under `data/.work/<cloud>/`; that
directory is git-ignored.

`app` accepts exactly one id. Comma-separated or whitespace-separated lists
are rejected by preflight and the offer commands before catalog/API access.

The workflow pulls the application from Contentful with
`--skip-offer-codes`, snapshots versions with `mk offer versions`, resolves live
AWS listings with `mk offer resolve`, builds
the preview, generates one live revision-bound plan per target, uploads the
plans and source snapshots as artifacts, then waits for approval on the
`<cloud>-marketplace-production` environment before running `mk offer apply`.
Step summaries are rendered by `mk offer report preflight|plan|apply` plus the
resolve table.

Live resolution:

- `mk offer resolve` loads the full product catalog (`--product-catalog`) to
  identify all trademarks in a live title. Title matches win, including titles
  belonging to other applications. Only if the title has no known trademark
  may the short description before its first English/Chinese comma be used.
  Multiple application matches are ambiguous; without an explicit code the
  run stops. It does not require the local offer-code matrix.
- With `--code`, that code is authoritative. A code whose title does not match
  the application is reported as an override and is still targeted.
- Without `--code`, every matching product is grouped by its detected offer
  variant. Each variant must map to exactly one code; two codes for the same
  variant stop the run as ambiguous.
- With `--offer`, only that variant is targeted. Combining `--code` and
  `--offer` forces the requested variant onto that code. Every explicit offer
  choice (including `base`) must be declared in that application's
  `multiOffers`; applications without the field do not support selection.
- Non-OS labels are detected from titles only. A summary saying "supports
  Multisite" is not evidence that the listing is the Multisite variant.
- Resolved targets are written into the temporary catalog, so build, plan and
  apply share the same code/EntityId/offer mapping.

Validation runs before any Contentful or AWS call:

- `mk offer doctor --cloud <cloud>` fails when the cloud has no plan/apply
  implementation in the CLI and validates the plan or apply role ARN with the
  same rule the AWS adapter uses.
- `mk offer preflight --skip-code-check` checks the application id against
  `product_en.json` without requiring codes from the local matrix.

Required repository configuration:

- Secret `CONTENTFUL_TOKEN`.
- Variable or secret `CONTENTFUL_SPACE_ID`; optional variable
  `CONTENTFUL_ENVIRONMENT` (default `master`).
- Organization-level values are not automatic: grant this repository access in
  Organization settings > Secrets and variables > Actions. A secret restricted
  to private repositories is unavailable to public repositories. The plan job
  prints non-secret availability booleans for the variable/secret contexts to
  distinguish a missing token from a missing space id.
- Variable `AWS_PLAN_ROLE_ARN` for read-only planning, or reuse `AWS_ROLE_ARN`.
  The plan role needs `ListEntities` and `DescribeEntity` so the EntityId can be
  resolved from the offer code.
- Protected environment `<cloud>-marketplace-production` (currently
  `aws-marketplace-production`) with required reviewers, optional self-review
  prevention and a `main` branch restriction.
- Environment variable `AWS_APPLY_ROLE_ARN` with write access to
  `DescribeEntity`, `DescribeChangeSet` and `StartChangeSet` for the target AMI
  product. If it is absent, the workflow falls back to `AWS_ROLE_ARN`.

The plan job uses read-only credentials. Its summary lists every target with its
Entity ARN, base revision and pending field count, plus before/after values for
each pending field. The apply job starts only after environment approval,
rechecks source snapshots and every live revision, and fails closed before
submitting anything if any target changed. It runs
`mk offer apply --wait-seconds 0`: success means AWS accepted every ChangeSet and
returned a ChangeSetId for each target, not that review, approval or publication
has completed. A partial submission reports `PARTIAL`, lists the submitted and
failed targets, and exits non-zero; re-running the workflow plans the remaining
targets, while already-updated targets plan to `NO_CHANGES`. The summary records
each ChangeSetId and plan hash; final status must be checked separately.

### Retrying a partial apply

AWS Marketplace has no transaction across entities, so a multi-target apply can
submit some targets and then fail. Do not retry the old plan and do not edit it:

1. Check each submitted ChangeSet with
   `mk offer status <change-set-id> --wait-seconds 600`. A ChangeSet that failed
   leaves its entity at the previous revision.
2. Re-run the workflow with the same application. The new plan describes every
   target again; targets whose content already matches produce `NO_CHANGES` and
   are not submitted.
3. Review the new artifact. Targets that were already updated show an empty
   diff, so only the remaining targets appear under "Pending updates".
4. Approve the apply job once for all remaining targets.

The apply output includes `resumeHint` when a run ends `PARTIAL`, and the job
summary lists the offer codes that were skipped. Because the plan hash binds the
entity revision, replaying a stale artifact cannot silently overwrite a target.

## Security boundary

`workflow_dispatch` requires repository write permission; read-only users and
anonymous visitors cannot start this workflow. The `plan` and `apply` jobs also
require the repository `Websoft9/marketplace` and `refs/heads/main`, as
defense in depth.

Write collaborators can still push a branch that modifies a workflow and
dispatch it, so workflow YAML is not the only security boundary. Protect `main`
with pull-request review, grant write access only to trusted maintainers, keep
the apply environment's required reviewers and prevent self-review, and scope
the Contentful token as narrowly as practical. Do not rely on the job-level
branch guard alone.

A job that uses a GitHub Environment has a different OIDC subject from a normal
branch job. The exact `sub` depends on the organization's OIDC subject template.
With the default template it is:

```text
repo:Websoft9/marketplace:environment:aws-marketplace-production
```

With immutable/custom subject claims it can include organization and repository
IDs, for example:

```text
repo:Websoft9@36851177/marketplace@326841278:environment:aws-marketplace-production
```

The apply workflow requests a short-lived OIDC token and prints only its decoded
`sub` and `aud`; copy that exact `sub` into the role trust policy. A plan role
may use the branch subject. Prefer separate read-only plan and write-only apply
roles. If one role is used for both, allow both subjects in its trust policy.
Re-run the workflow after updating the trust relationship; no workflow change
can bypass the IAM trust condition. The reviewed artifact contains
`catalog.json`, `apps-verson.json`, `preview.json` and `plan.json`.
