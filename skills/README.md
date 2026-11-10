# Repository Skills

Repository-specific agent skills belong in `skills/<skill-name>/SKILL.md`. Use a lowercase kebab-case directory name and YAML `name` and `description` metadata. Each skill should specify its data sources, steps, stopping conditions, and expected output.

## Available Skills

- [Marketplace product content](marketplace-product-content/SKILL.md): Author personalized AWS AMI listing content from the temporary Contentful catalog, vendor policy and AWS listing rules, validate drafts, and prepare revision-bound Catalog API metadata updates.

- [Application content gen](app-content-gen/SKILL.md): Pull selected applications, or pull and regenerate selected fields, validate the result mechanically, and report. Omitting fields runs pull-only mode. Does not push to Contentful.
- [Application content push](app-content-push/SKILL.md): Validate and push selected catalog fields to Contentful with a dry run first, drafts by default, and optional publishing.
- [Aliyun marketplace image version check](aliyun-product-freshness/SKILL.md): Check a supplier's published and visible image products and summarize outdated versions in one GitHub issue.
- [Offer code verify](offer-code-verify/SKILL.md): Validate local marketplace offer codes, fetch listing titles for duplicate and multi-code entries, and recommend which application each code belongs to. Advisory only.
- [Marketplace search visibility](marketplace-search-visibility/SKILL.md): Search a marketplace for keywords and report whether and where Websoft9 products appear. Preliminary framework.
- [Product expressiveness](product-expressiveness/SKILL.md): Check a marketplace product's expression and compliance evidence, such as demo images, videos, cases, qualifications, and versions. Preliminary placeholder.

The [Marketplace Product Freshness workflow](../.github/workflows/product-freshness.yml) registers and invokes the selected skill. It can run on a schedule or be started manually from the repository's **Actions** tab.
