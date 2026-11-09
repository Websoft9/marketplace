# Issue-driven offer updates (paused)

The former product-file Issue workflow is **paused** after moving application
facts out of the repository.

Historical status:

- Application facts now come temporarily from
  `data/.catalog/catalog.json`; the repository no longer stores
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

`build` writes `data/.catalog/aws/odoo.json`. `plan` remains live,
revision-bound and is the only step that prepares a submit-ready ChangeSet
request. Publication requires required source fields, a resolved Catalog
EntityId and an explicitly reviewed plan.

## Manual GitHub Actions apply workflow

`.github/workflows/offer-apply.yml` runs manually for one
application and one cloud. Inputs are the catalog application id, an optional
display name used for the run label, an optional comma-separated list of offer
codes, and a cloud selector (currently only `aws`). When no offer codes are
given, every code recorded for the application is targeted. It does not publish
files committed under `data/.catalog/<cloud>/`; that directory is git-ignored.
The workflow builds the preview there, resolves every target EntityId, generates
one live revision-bound plan per target, uploads the plans and source snapshots
as artifacts, then waits for approval on the `<cloud>-marketplace-production`
environment before running `mk offer apply`.

Validation runs before any Contentful or AWS call:

- `Validate inputs` fast-fails on characters that cannot appear in an
  application id and rejects unknown clouds. Case and existence are reported by
  `mk offer preflight`, so the correction message lives in one place.
- `mk offer preflight` checks the application id against `product_en.json`,
  suggests close or lowercase matches, and verifies that every requested offer
  code belongs to that application. Offer codes come from the repository folder
  `data/offer-codes/`, not a remote `offer.json`. A code belonging to another
  application fails here instead of updating the wrong product.
- `mk offer targets` resolves each offer code to a Catalog EntityId and records
  `targets` in the temporary catalog, so later steps do not repeat catalog
  lookups. Codes that resolve to one entity collapse into a single target, and
  the code-to-EntityId map is cached under `/tmp/opencode/` with `actions/cache`;
  cached codes are re-verified by `DescribeEntity` instead of scanning every
  product, and the cache is not uploaded with the plan artifact.

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
