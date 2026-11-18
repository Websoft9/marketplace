# AppStore Screenshots

## Workflow

- Manually trigger GitHub Actions; process all application IDs from the catalog URL in `config/app.json` (`sources.catalog`).
- Use Playwright to log in to Websoft9, enter AppStore, search each application ID, and open the matching application's details.
- Wait for detail content and images to load, then capture a **1920 × 1040** viewport with device scale factor `1` as PNG. Exclude browser chrome; do not capture a scrolling full page.
- Upload to `s3://assets-797851739507-us-east-1-an/offers/{appid}-installation.png`, overwriting any existing object.
- Process sequentially. Continue after individual failures and summarize results. Actions uses `--allow-app-failures`: application failures produce warnings, not a failed workflow. Configuration, catalog, login and other fatal setup failures still fail the run. Local CLI remains strict unless this flag is supplied.
- Never upload a screenshot when the match is missing or ambiguous, or details fail to load; preserve the existing object.

## Configuration

- Local credentials: `.secrets/websoft9.env` (`WEBSOFT9_URL`, `WEBSOFT9_USER`, `WEBSOFT9_PASSWORD`). Actions receives these through Secrets; never commit credentials or expose them in logs or artifacts.
- Reuse existing AWS OIDC authentication; verify the role can write the target S3 prefix. Do not change bucket access policies.
- Verify runner connectivity and actual page locators, then validate one application before a full run.

## Usage

```sh
make install
make cli ARGS="tools install-browser"
make cli cloud=websoft9 ARGS="asset screenshots --apps n8n --no-upload"
make cli cloud=websoft9 ARGS="asset screenshots"
```

- `make install` creates `.venv` if needed and installs the project plus all declared extras (`aws`, `aliyun`, `screenshots`). Override `CLI_VENV` to use another virtual environment directory.
- It also installs setuptools for `make cli`'s offline editable refresh. `make cli` never deletes an existing environment to repair missing dependencies; run `make install` with the same `CLI_VENV` instead.
- `mk tools install-browser` installs Chromium using the same Python environment as `mk`. On a fresh Linux host missing OS libraries, install Playwright's system dependencies separately; the command does not invoke `apt` or require root.
- `mk asset screenshots` is the shared local/Actions entry point. Omit `--apps` for all; use comma-separated IDs for a subset. `--no-upload` never initializes AWS.
- Output defaults to `<repository-root>/data/.work/screenshots/` regardless of the current working directory: PNG files and a sanitized `report.json`. This work directory is Git-ignored; `--output-dir` overrides it. `--summary PATH` appends a Markdown report.
- `.github/workflows/appstore-screenshots.yml` runs manually on `main`. Configure `WEBSOFT9_URL`, `WEBSOFT9_USER`, `WEBSOFT9_PASSWORD` as Actions Secrets; reuse the existing `AWS_ROLE_ARN` variable with `s3:PutObject` permission on the target prefix. Bucket policy continues to control public access.
- Default runner: `ubuntu-latest`; optionally set `APPSTORE_SCREENSHOTS_RUNNER` to a reachable Linux runner label. Runs are serialized and bounded to 60 minutes to stay within the one-hour AWS session.
- The workflow retains only the sanitized report for seven days and removes local PNGs. For visual validation use local `--no-upload`; the Action's `no_upload` option validates capture but does not retain PNGs.
- Match one exact catalog trademark when present. With a missing/blank trademark, require exactly one search result instead; never guess a display name. Both paths require a detail GitHub link identifying the same `appid`. Missing/ambiguous matches, wrong details and unloaded visible images fail only that application, without uploading; continue the batch and report failures.
- Loading and empty-results cards are not application results and must never be opened or captured.
- Summary includes a copyable `Failed list: app1, app2` line (`none` if empty), followed by the unchanged per-application table.
- Search reasons distinguish `no_search_results` (explicit empty state), `multiple_matching_results` (ambiguous candidates), `trademark_not_matched` (results but no exact catalog name), `search_loading_timeout` (still loading), and `search_results_timeout` (no conclusive result state). The summary explains each reason in English.
- Check the login response before waiting for AppStore. A confirmed authenticated session may refresh once to recover a stalled frontend redirect; never retry credential submission. Report rejected credentials, rate limiting, service errors and navigation failures separately, without response bodies.
