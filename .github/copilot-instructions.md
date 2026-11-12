Read the root `AGENTS.md` first, then consult the relevant files in `specs/`. Reusable domain workflows live in `.agents/skills/`; follow their data-source, privacy, and issue-creation requirements. Do not change existing cloud-platform JSON schemas or GitHub Actions behavior without a task-specific reason.

Handle issues created from `.github/ISSUE_TEMPLATE/update_app_basic_info.md` with the `app-content-gen` skill in `.agents/skills/app-content-gen/`.
