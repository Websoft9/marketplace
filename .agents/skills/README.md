# Repository Skills

Repository-specific agent skills belong in `.agents/skills/<skill-name>/SKILL.md`. GitHub Copilot cloud agent, Copilot CLI, and OpenCode V2 automatically discover this directory. Use a lowercase kebab-case directory name and YAML `name` and `description` metadata. Each skill should specify its data sources, steps, stopping conditions, and expected output.

## Available Skills

- [Marketplace product content](marketplace-product-content/SKILL.md): Author personalized AWS AMI listing content from the temporary Contentful catalog, vendor policy and AWS listing rules, validate drafts, and prepare revision-bound Catalog API metadata updates.

- [Application content gen](app-content-gen/SKILL.md): Map explicit user intent to selected fields, read field/category references on demand, gather evidence and generate content; delegate strict validation, failed-app restoration and safe local commits to `mk catalog apply`. Pure pull requests or requests without generation intent/field selection run pull-only. Shows a full before/after comparison and offers a separate Contentful push (drafts or publish) for actual validated generation changes; never pushes automatically.
- [Application content push](app-content-push/SKILL.md): Review a field-by-field before/after dry-run comparison, then choose drafts only, update and publish in one step, or cancel; `mk catalog push` owns mechanical validation and platform writes.
- [Aliyun marketplace image version check](aliyun-product-freshness/SKILL.md): Check a supplier's published and visible image products and summarize outdated versions in one GitHub issue.
- [Marketplace operations check](marketplace-operations-check/SKILL.md): Standalone multi-cloud check of selected listings for offer codes, listing status, version information and basic content. Read-only reports; per-check and per-platform references; no other skills invoked.
- [Offer code verify](offer-code-verify/SKILL.md): Validate and find local marketplace offer codes, investigate listing titles, and propose scoped maintenance commands. Advisory only; read-only seller API checks require explicit opt-in.
- [Marketplace search visibility](marketplace-search-visibility/SKILL.md): Search a marketplace for keywords and report whether and where Websoft9 products appear. Preliminary framework.
- [Product expressiveness](product-expressiveness/SKILL.md): Check a marketplace product's expression and compliance evidence, such as demo images, videos, cases, qualifications, and versions. Preliminary placeholder.

The [Marketplace Product Freshness workflow](../../.github/workflows/product-freshness.yml) registers and invokes the selected skill. It can run on a schedule or be started manually from the repository's **Actions** tab.
