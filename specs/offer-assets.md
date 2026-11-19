# Wave-based AWS asset refresh

Asset publishing is an explicit operation, not image synchronization. Prepare
and upload the wave's images to fixed URLs, review them, then publish selected
offers through the existing revision-bound plan/apply executor. The manual
`offer-apply.yml` workflow supports this through its **Asset** update type;
no real cloud writes are made by tests.

## CLI

First prepare the temporary application catalog and resolved public targets
using `mk catalog pull` and `mk offer resolve`. Then:

```sh
mk asset screenshots --apps odoo
mk offer assets build --app odoo --output assets.json
mk offer plan --app odoo --assets assets.json --output assets-plan.json
mk offer report plan --input assets-plan.json --cloud aws
# Review the actual plan, then acknowledge its exact planHash.
mk offer apply --app odoo --assets assets.json --plan assets-plan.json \
  --approve-plan-hash REVIEWED_PLAN_HASH
```

Use the same `--catalog FILE` for non-default catalogs and `--profile NAME` for
local AWS credentials. Target selection supports `plan --all-codes` and
`--offer-code CODE`; it never discovers additional targets implicitly.

## Template and images

- `data/listing/templates/aws-ami-assets.json` defines one `baseUrl` and relative
  image paths, e.g. `offers/{catalog.id}-installation.png`. Build expands catalog
  placeholders and joins the base, producing ordinary complete HTTPS URLs.
- `baseUrl` must be static unsigned HTTPS; relative paths cannot escape it.
  Existing absolute-URL v2 templates and catalog-sourced v1 templates still work.
- `mk asset screenshots` generates/uploads installation PNGs at the existing
  fixed S3 paths, allowing overwrite. Shared images/logo are prepared separately
  at the template's fixed paths. URL configuration does not reconfigure the
  screenshot uploader's bucket or access policy.
- There is no staging command, image SHA/manifest, hash/version path, S3 download
  or content-change/existence check in plan/apply. Finish uploads and visual
  checks first; do not overwrite source images until the wave completes.
- Missing remote files are not silently dropped or treated as image removals.
  The full authored list is submitted; AWS processing/status and Catalog read-back
  are checked, but do not prove which pixels are visible on the public page.

## Refresh semantics and protections

- `--assets` explicitly requests resubmission of every authored asset field,
  **even when its URL is unchanged**. This uses the same-URL refresh behavior
  verified by the operator. Ordinary copy updates retain their diff/NO_CHANGES
  behavior and do not refresh assets.
- PromotionalMedia is sent as the complete authored list of 1–5 images. Omitted
  fields preserve their remote values; empty media lists and copy/video injection
  are rejected. For logo-only updates, omit PromotionalMedia before planning.
  Image-only plans refuse to replace a collection containing videos.
- Each newly generated asset plan has an automatic operation ID so a fresh wave
  gets a new idempotency token even if AWS metadata/revision remains identical.
  It is an internal request identity, **not an image version**. Applying the same
  still-valid plan reuses its token; a changed revision requires a new plan/review.
- Existing source/plan hashes bind the asset JSON, catalog, target and revision,
  not image bytes. Apply regenerates/compares the plan and requires explicit
  `--approve-plan-hash`. Keep these publication protections; no picture metadata
  ledger or custom publish executor is needed.
- Apply defaults to `--wait-seconds 0`: return SUBMITTED and ChangeSet IDs when
  AWS accepts the requests, without polling or post-submit read-back
  (`readBackVerified: false`). This does not mean processing, platform review or
  public-page publication is complete. Pre-submit revision/hash checks still run.
- Optionally pass `--wait-seconds 300` (or another positive timeout) to wait for
  ChangeSet SUCCEEDED and matching **field** read-back, performed without forcing
  another refresh. Timeout/mismatch fails as UNVERIFIED, retaining IDs; no
  automatic cancellation/resubmission. Multi-target writes may be partial.

## GitHub Actions

In **Marketplace Offer Apply**, choose **Asset** under **Update type** (below
cloud). **Product information** is the default and does not update assets. Each
run performs only one operation. Asset mode builds the asset JSON once, plans
with `--assets`, uploads the JSON and plan in the existing artifact, then waits
for the same production Environment approval. Apply downloads the reviewed
files and supplies `--assets`, the exact reviewed `--approve-plan-hash`, and
`--wait-seconds 0`; it never rebuilds assets after approval.

Prepare/upload/check images separately before starting the workflow; this Action
does not generate screenshots or upload images. Keep sources unchanged until AWS
finishes processing, not merely until the submit-only Action finishes. Product
code can limit the target; Label must remain Auto in Asset mode. Existing target
resolution (including its catalog/version snapshot dependencies) is shared.
The run name and summaries identify the selected operation, and both modes share
the application/cloud concurrency group to avoid simultaneous revision changes.

Rerunning an Asset workflow creates a new explicit refresh, including previously
updated targets; it is not the Product information NO_CHANGES retry behavior.
After partial submission, inspect existing ChangeSets before planning remaining
targets (use product code to limit scope).

Old staged manifests/plans are not reused: regenerate build JSON and the plan.
Existing old S3 hash objects are not deleted. CLI hash acknowledgment is not a
substitute for the Action's Environment approval. IAM and bucket policies are
unchanged; asset publishing uses the existing Marketplace write role, not S3 writes.
