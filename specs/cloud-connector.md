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

## Aliyun China Marketplace
The `aliyun` option in `.github/workflows/offer-listing-check.yml` uses GitHub OIDC to assume a RAM role. Set the `ALIYUN_ROLE_ARN` and `ALIYUN_OIDC_PROVIDER_ARN` GitHub Actions Variables. The workflow lists front-end-visible mirror products from `长沙网久软件有限公司` and extracts the application version from each product summary.

When OIDC returns temporary credentials, the CLI builds the Aliyun client with an explicit `StsTokenCredential` and hides `ALIYUN_ACCESS_KEY_ID`/`ALIYUN_ACCESS_KEY_SECRET` only while the SDK chooses its signer. `aliyunsdkcore` otherwise prefers those environment keys over the explicit credential, which drops `SecurityToken` and makes the request fail.

## Alibaba Cloud International Marketplace
The `alibabacloud` option in `offer-list.yml` reads the public Websoft9 store listing and shows image products with their detected application versions. The store inventory used by `mk store products` and `mk offer-codes gen` is stricter: it reads the authenticated Market `DescribeProducts` API and intersects those facts with the public storefront. That path requires `ALIBABA_CLOUD_*` credentials; the public storefront itself remains unauthenticated.

`.github/workflows/offer-codes-matrix.yml` assumes a RAM role for the international account through GitHub OIDC, and the exported `ALIBABA_CLOUD_*` credentials are consumed by the inventory query. Set `ALIBABACLOUD_ROLE_ARN` and `ALIBABACLOUD_OIDC_PROVIDER_ARN` as Actions variables; the `ALIBABA_CLOUD_ROLE_ARN` and `ALIBABA_CLOUD_OIDC_PROVIDER_ARN` names are accepted as aliases. The role trust policy uses the GitHub OIDC audience `sts.aliyuncs.com`. This account is separate from Aliyun China; never reuse Aliyun China's `ALIYUN_*` credentials.
