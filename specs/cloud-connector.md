# Cloud Connector

## Generate a local configuration template

Use the Make target to generate a ready-to-load environment file for a cloud
platform key from `config/marketplace.json`, or for the non-cloud targets
`contentful`, `wecom` and `websoft9`:

```sh
make cloud-configure cloud=aws
make cloud-configure cloud=contentful
make cloud-configure cloud=wecom
make cloud-configure cloud=websoft9
# generate one file for every target (existing files are skipped)
make cloud-configure cloud=all
# or, using this repository's ARGS convention:
make cloud-configure ARGS="--cloud aws"
```

The uppercase legacy form `CLOUD=aws` is also accepted. The default output is
`.secrets/<cloud>.env`, which is git-ignored and created with mode `600`. It
contains only that target's keys, with blank values for the required secrets.
Fill only the blanks.

- `contentful` writes `CONTENTFUL_SPACE_ID`, `CONTENTFUL_TOKEN`,
  `CONTENTFUL_ENVIRONMENT` and optional API endpoint overrides.
- `wecom` writes `WECOM_WEBHOOK` for best-effort notifications.
- `websoft9` writes `WEBSOFT9_URL` (default
  `http://t.inner.websoft9.cn:9000/`), `WEBSOFT9_USER` and
  `WEBSOFT9_PASSWORD`.
- `aliyun` writes `ALIYUN_ACCESS_KEY_ID`, `ALIYUN_ACCESS_KEY_SECRET` and
  `ALIYUN_SECURITY_TOKEN`.
- `alibabacloud` writes `ALIBABA_CLOUD_*` and is a separate Alibaba Cloud
  account from Aliyun China; never reuse one site's key for the other.
- `azure` writes `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`
  for the Websoft9 Partner Center publisher (`websoft9inc`).
- `azure-vmlab` writes the same local `AZURE_*` keys to `azure-vmlab.env` for
  the independent VMLab account; the CLI loads these as `AZURE_VMLAB_*`.
- `huaweiyun` retains local `HUAWEICLOUD_ACCESS_KEY_ID/SECRET` fields for future
  authenticated use; current store inventory/matrix uses the public guest search
  and does not consume them.
- `huaweicloud` also uses public international seller search without an account
   or credentials; its legacy optional env template is not needed for store/matrix.
- `googlecloud` store/listing queries and matrix generation use public console
  browse, without account credentials, ADC or OIDC. No env file is needed;
  any legacy Google credential template is unrelated to this read-only adapter.
- Cloud files contain only that cloud's credentials; services are never mixed
  into a cloud file.

Load it explicitly:

```sh
mk --env-file .secrets/aws.env offer-codes gen --platform aws
```

Or let `make cli` load it for the whole session/one-shot command:

```sh
make cli cloud=aws
make cli cloud=aws ARGS="offer-codes gen --platform aws"
```

`mk` also reads the `MK_ENV_FILE` environment variable. Existing environment
variables win over values from the file, so a shell export always overrides the
local file. Values are never printed.

Use `ARGS="--cloud aws --output /path/to/file"` to choose another path. An
existing output file is never overwritten unless `--force` is passed:

```sh
make cloud-configure ARGS="--cloud aws --force"
```

GNU Make parses its own command-line options before running targets, so the
literal form `make cloud-configure --cloud aws` is not valid; pass `cloud=aws`
or put the CLI option inside `ARGS` as shown above.

## AWS 
GitHub Actions authenticates to AWS through GitHub OIDC and assumes a least-privilege IAM role, so no long-lived AWS access keys are stored in the repository. Trust policies must use this repository's immutable subject prefix `repo:Websoft9@36851177/marketplace@326841278` and allow only the intended branch or environment.

## Huawei Cloud China and International (`huaweiyun`, `huaweicloud`)

The current store query and code generator use the official public seller guest
search endpoint, not an authenticated seller-list OpenAPI. They do not consume
local AK/SK or temporary credentials, so the Matrix Action does not request
Huawei OIDC tokens or require Huawei repository variables for this path.
Existing cloud-side OIDC configuration and local `.secrets/huaweiyun.env` values
are left untouched; no credentials are forwarded to the public endpoint.
This integration does **not** validate those credentials, the configured OIDC
trust or seller backend permissions. Do not equate public seller metadata with
proof of the caller's account identity. See [store-products.md](store-products.md)
for source/schema limitations and completeness checks.

Both `mk store products --platform huaweiyun --json` and
`mk offer-codes gen --platform huaweiyun --dry-run --json` work without secrets.
Loading `.secrets/huaweiyun.env` explicitly remains supported but has no effect
on this adapter. International queries likewise work with
`mk store products --platform huaweicloud --json` and
`mk offer-codes gen --platform huaweicloud --dry-run --json`, **without** an env
file, account or OIDC configuration. Both platforms are included in the Matrix
Action's `all`, using separate verified public sources. Authenticated product
updates remain unsupported. A future authenticated seller adapter must verify the
actual Marketplace API, required permissions and its STS-token support before
claiming OIDC-backed inventory; generic Huawei IAM/IMS access is not equivalent.

## Google Cloud Marketplace (`googlecloud`)

`mk offer list --platform googlecloud --json` (or `mk store products`) and
`mk offer-codes gen --platform googlecloud --dry-run --json` need no Google
account, service-account JSON file, ADC, token, OIDC or SDK. They use the
unauthenticated Websoft9 public console store, validating the configured partner
and publisher namespace. The console's public frontend bootstrap key is read
at runtime, kept only in memory and never included in output. The Matrix Action
includes Google Cloud in `all` without a credential setup step. This does not
validate any account/producer permission and does not implement listing writes.
See [store-products.md](store-products.md) for the verified interface and its
fail-closed schema/pagination checks.

## Azure Marketplace (Websoft9 and VMLab)

Associate the Entra application with the Websoft9 Partner Center publisher
account and grant the Marketplace API permissions/role required by Microsoft's
[Product Ingestion onboarding](https://learn.microsoft.com/en-us/partner-center/marketplace-offers/product-ingestion-api#getting-started).
The CLI obtains a token with `client_credentials`, tenant/client ID, client secret
and `scope=https://graph.microsoft.com/.default`; no Azure subscription ID, ARN,
`azure/login` or GitHub OIDC configuration is required by this adapter.

Local usage: `mk --env-file .secrets/azure.env store products --platform azure --json`.
For the Matrix Action configure `AZURE_TENANT_ID` and `AZURE_CLIENT_ID` as Actions
secrets or variables, and `AZURE_CLIENT_SECRET` as an **Actions secret**. Credentials
are scoped to the check/generation steps, not passed to third-party OIDC actions.
The Matrix Action isolates account setup failures: it records a failed platform
and continues other accounts, while preserving a failed final run and blocking
R2 publication. Authentication requests retry only transient network/throttling
failures; bad client credentials/permissions are not retried. See
[store-products.md](store-products.md) for timeouts and retry limits and
[offer-codes.md](offer-codes.md) for per-platform status artifacts.
No secret/token or raw seller response is printed or written. The public Catalog
`/offers` interface used for visibility is unauthenticated; the newer `/products`
API and its API key are not used. See [store-products.md](store-products.md).

Only read-only product queries and local matrix generation are implemented;
Azure plan/apply remains unsupported for both publishers.

### VMLab account isolation

GitHub uses `AZURE_VMLAB_TENANT_ID` and `AZURE_VMLAB_CLIENT_ID` (secrets or
variables), and `AZURE_VMLAB_CLIENT_SECRET` (**secret**). These never replace
the default `AZURE_*` credentials. The Matrix Action adds `azure-vmlab` as a
selection, checks its own credential set, and includes both Azure accounts in
`all`. Missing/partial credentials do not fall back to the other account.

Local files may keep the same `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`,
`AZURE_CLIENT_SECRET` field names. The CLI recognizes the conventional filename
`azure-vmlab.env` (including `MK_ENV_FILE`) and maps those three keys to the
VMLab namespace before loading, preserving any Websoft9 credentials already
in the environment. Existing **namespaced** environment values still win.
An arbitrary filename does not select an account: use `AZURE_VMLAB_*` keys
in such a file instead. Neither file is automatically discovered or loaded.

```sh
mk --env-file .secrets/azure.env store products --platform azure --json
mk --env-file .secrets/azure-vmlab.env store products --platform azure-vmlab --json
mk --env-file .secrets/azure-vmlab.env offer-codes gen --platform azure-vmlab --dry-run --json
```

For simultaneous-account queries, load the Websoft9 `AZURE_*` set and VMLab
`AZURE_VMLAB_*` set into the environment, or load `.secrets/azure.env` first
and use `--env-file .secrets/azure-vmlab.env` for the second account:
`mk --env-file .secrets/azure-vmlab.env store products --platform azure --platform azure-vmlab --json`.
Do not source both generic-key files directly; the second shell source would
overwrite the first account's values. The adapter passes each account's token
as an explicit request argument and keeps concurrent queries independent.

## Aliyun China Marketplace
The `aliyun` option in `.github/workflows/offer-listing-check.yml` uses GitHub OIDC to assume a RAM role. Set the `ALIYUN_ROLE_ARN` and `ALIYUN_OIDC_PROVIDER_ARN` GitHub Actions Variables. The workflow lists front-end-visible mirror products from `长沙网久软件有限公司` and extracts the application version from each product summary.

When OIDC returns temporary credentials, the CLI builds the Aliyun client with an explicit `StsTokenCredential` and hides `ALIYUN_ACCESS_KEY_ID`/`ALIYUN_ACCESS_KEY_SECRET` only while the SDK chooses its signer. `aliyunsdkcore` otherwise prefers those environment keys over the explicit credential, which drops `SecurityToken` and makes the request fail.

## Alibaba Cloud International Marketplace
The `alibabacloud` option in `offer-list.yml` reads the public Websoft9 store listing and shows image products with their detected application versions. The store inventory used by `mk store products` and `mk offer-codes gen` is stricter: it reads the authenticated Market `DescribeProducts` API and intersects those facts with the public storefront. That path requires `ALIBABA_CLOUD_*` credentials; the public storefront itself remains unauthenticated.

`.github/workflows/offer-codes-matrix.yml` assumes a RAM role for the international account through GitHub OIDC, and the exported `ALIBABA_CLOUD_*` credentials are consumed by the inventory query. Set `ALIBABACLOUD_ROLE_ARN` and `ALIBABACLOUD_OIDC_PROVIDER_ARN` as Actions variables; the `ALIBABA_CLOUD_ROLE_ARN` and `ALIBABA_CLOUD_OIDC_PROVIDER_ARN` names are accepted as aliases. The role trust policy uses the GitHub OIDC audience `sts.aliyuncs.com`. This account is separate from Aliyun China; never reuse Aliyun China's `ALIYUN_*` credentials.
